# s1d1-tantive as one ECS Fargate service in a VPC and cluster you already have. Adapt: vpc_id, subnet_ids,
# cluster_arn, ingress_cidr and the image/cpu/memory trio (full: 2048 + 4096; small: 1024 + 2048).
terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

variable "vpc_id" {
  description = "Existing VPC of the service."
  type        = string
}

variable "subnet_ids" {
  description = "Existing subnets of the tasks, in that VPC."
  type        = list(string)
}

variable "assign_public_ip" {
  description = "true in public subnets (needed to pull from ghcr.io); false behind a NAT gateway or a registry mirror."
  type        = bool
}

variable "cluster_arn" {
  description = "ARN of an existing ECS cluster."
  type        = string
}

variable "ingress_cidr" {
  description = "Who may reach port 8788, as an IPv4 CIDR. The VPC CIDR keeps it inside; 0.0.0.0/0 publishes it (then set s1d1_api_key)."
  type        = string
}

variable "s1d1_tiers" {
  description = "S1D1_TIERS, e.g. TRIVIAL=0.15,LOW=0.4,MEDIUM=0.7,HIGH=0.9,EXPERT=1.0. Must match the tier names your gateway sends, otherwise the service answers 422."
  type        = string
}

variable "s1d1_api_key" {
  description = "S1D1_API_KEY, kept in Secrets Manager and injected as a container secret. Empty = no key."
  type        = string
  default     = ""
  sensitive   = true
}

variable "image" {
  description = "Image to run, one per model variant (full needs 2 GiB, small 1 GiB, tiny 512 MiB)."
  type        = string
  default     = "ghcr.io/scombat/s1d1-tantive:0.6.0-full-onnx-int8"
}

variable "cpu" {
  description = "Task CPU units; the pair with memory must be one Fargate allows."
  type        = number
  default     = 2048
}

variable "memory" {
  description = "Task memory in MiB."
  type        = number
  default     = 4096
}

variable "desired_count" {
  description = "Number of tasks."
  type        = number
  default     = 1
}

locals {
  has_key = var.s1d1_api_key != ""
}

resource "aws_cloudwatch_log_group" "tantive" {
  name              = "/ecs/s1d1-tantive"
  retention_in_days = 30
}

resource "aws_secretsmanager_secret" "api_key" {
  count = local.has_key ? 1 : 0
  name  = "s1d1-tantive/api-key"
}

resource "aws_secretsmanager_secret_version" "api_key" {
  count         = local.has_key ? 1 : 0
  secret_id     = aws_secretsmanager_secret.api_key[0].id
  secret_string = var.s1d1_api_key
}

resource "aws_iam_role" "execution" {
  name = "s1d1-tantive-execution"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# the role may read the one secret it injects, nothing else
resource "aws_iam_role_policy" "read_api_key" {
  count = local.has_key ? 1 : 0
  name  = "read-api-key"
  role  = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "secretsmanager:GetSecretValue"
      Resource = aws_secretsmanager_secret.api_key[0].arn
    }]
  })
}

resource "aws_security_group" "tantive" {
  name        = "s1d1-tantive"
  description = "s1d1-tantive: port 8788 from ingress_cidr"
  vpc_id      = var.vpc_id

  ingress {
    description = "service port"
    from_port   = 8788
    to_port     = 8788
    protocol    = "tcp"
    cidr_blocks = [var.ingress_cidr]
  }
  # outbound is HTTPS only, to pull the image from ghcr.io (a public registry has no fixed address), ship logs and OTLP
  egress {
    description = "HTTPS out"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    #trivy:ignore:AVD-AWS-0104
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_ecs_task_definition" "tantive" {
  family                   = "s1d1-tantive"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = aws_iam_role.execution.arn

  container_definitions = jsonencode([{
    name         = "tantive"
    image        = var.image
    essential    = true
    portMappings = [{ containerPort = 8788, protocol = "tcp" }]
    environment = [
      { name = "S1D1_TIERS", value = var.s1d1_tiers },
      { name = "S1D1_FORMATS", value = "systemone,decisions,classify" },
      { name = "S1D1_DEADLINE_MS", value = "800" },
    ]
    secrets = local.has_key ? [{ name = "S1D1_API_KEY", valueFrom = aws_secretsmanager_secret.api_key[0].arn }] : []
    # ready only once the model is loaded; the image has no curl, so the check uses Python
    healthCheck = {
      command     = ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8788/ready', timeout=3).status == 200 else 1)"]
      interval    = 15
      timeout     = 5
      retries     = 3
      startPeriod = 60
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.tantive.name
        "awslogs-region"        = data.aws_region.current.region
        "awslogs-stream-prefix" = "tantive"
      }
    }
  }])
}

data "aws_region" "current" {}

resource "aws_ecs_service" "tantive" {
  name            = "s1d1-tantive"
  cluster         = var.cluster_arn
  task_definition = aws_ecs_task_definition.tantive.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [aws_security_group.tantive.id]
    assign_public_ip = var.assign_public_ip
  }
}

# no load balancer: a task is reached on its own IP; put an ALB (target type ip, health check /ready) in front for production
output "url" {
  description = "Service name; find the task IP in the ECS console (port 8788), or put a load balancer in front."
  value       = "ecs://${aws_ecs_service.tantive.name}:8788"
}
