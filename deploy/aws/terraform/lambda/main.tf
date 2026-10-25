# s1d1-tantive as an AWS Lambda container function with provisioned concurrency. The image is the one built from
# deploy/aws/Dockerfile.lambda (FROM ghcr.io/scombat/s1d1-tantive:0.8.1-full-onnx-int8 plus the Lambda Web Adapter),
# pushed to ECR in this region. Adapt: image_uri, architecture, memory_size (full: 4096; small: 2048).
terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

variable "image_uri" {
  description = "ECR image built from Dockerfile.lambda (Lambda cannot pull from ghcr.io). Use a new tag or a digest per build."
  type        = string
}

variable "architecture" {
  description = "x86_64 or arm64: the one the image was built for."
  type        = string
  default     = "x86_64"
}

variable "memory_size" {
  description = "MB. 4096 suits full, 2048 suits small."
  type        = number
  default     = 4096
}

variable "timeout" {
  description = "Seconds per invocation, a cold start included."
  type        = number
  default     = 30
}

variable "provisioned_concurrency" {
  description = "Warm instances, billed all the time. 1 or more = no cold start (suits a gateway such as LiteLLM). 0 = none: the first call after a pause waits several seconds for the model."
  type        = number
  default     = 1
}

variable "url_auth_type" {
  description = "Function URL: AWS_IAM (callers sign requests) or NONE (public: then set s1d1_api_key)."
  type        = string
  default     = "AWS_IAM"
  validation {
    condition     = contains(["AWS_IAM", "NONE"], var.url_auth_type)
    error_message = "url_auth_type is AWS_IAM or NONE."
  }
}

variable "s1d1_tiers" {
  description = "S1D1_TIERS, e.g. TRIVIAL=0.15,LOW=0.4,MEDIUM=0.7,HIGH=0.9,EXPERT=1.0. Must match the tier names your gateway sends, otherwise the service answers 422."
  type        = string
}

variable "s1d1_api_key" {
  description = "S1D1_API_KEY, an environment variable of the function (encrypted at rest, readable by whoever can read its configuration). Empty = no key."
  type        = string
  default     = ""
  sensitive   = true
}

resource "aws_cloudwatch_log_group" "tantive" {
  name              = "/aws/lambda/s1d1-tantive"
  retention_in_days = 30
}

resource "aws_iam_role" "tantive" {
  name = "s1d1-tantive-lambda"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "logs" {
  role       = aws_iam_role.tantive.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "tantive" {
  function_name = "s1d1-tantive"
  package_type  = "Image"
  image_uri     = var.image_uri
  architectures = [var.architecture]
  memory_size   = var.memory_size
  timeout       = var.timeout
  role          = aws_iam_role.tantive.arn
  publish       = true # a new version per image, which the alias below follows

  environment {
    variables = merge(
      {
        S1D1_TIERS       = var.s1d1_tiers
        S1D1_FORMATS     = "systemone,decisions,classify"
        S1D1_DEADLINE_MS = "800"
        # the Web Adapter reads the port from the image (AWS_LWA_PORT=8788, readiness on /ready)
        AWS_LWA_PORT = "8788"
      },
      var.s1d1_api_key == "" ? {} : { S1D1_API_KEY = var.s1d1_api_key },
    )
  }

  depends_on = [aws_cloudwatch_log_group.tantive, aws_iam_role_policy_attachment.logs]
}

resource "aws_lambda_alias" "live" {
  name             = "live"
  function_name    = aws_lambda_function.tantive.function_name
  function_version = aws_lambda_function.tantive.version
}

resource "aws_lambda_provisioned_concurrency_config" "warm" {
  count                             = var.provisioned_concurrency > 0 ? 1 : 0
  function_name                     = aws_lambda_function.tantive.function_name
  qualifier                         = aws_lambda_alias.live.name
  provisioned_concurrent_executions = var.provisioned_concurrency
}

resource "aws_lambda_function_url" "live" {
  function_name      = aws_lambda_function.tantive.function_name
  qualifier          = aws_lambda_alias.live.name
  authorization_type = var.url_auth_type
}

# a public URL needs both permissions; with AWS_IAM the callers' own policies decide
resource "aws_lambda_permission" "public_url" {
  count                  = var.url_auth_type == "NONE" ? 1 : 0
  statement_id           = "PublicUrl"
  function_name          = aws_lambda_function.tantive.function_name
  qualifier              = aws_lambda_alias.live.name
  action                 = "lambda:InvokeFunctionUrl"
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "public_invoke" {
  count                    = var.url_auth_type == "NONE" ? 1 : 0
  statement_id             = "PublicInvoke"
  function_name            = aws_lambda_function.tantive.function_name
  qualifier                = aws_lambda_alias.live.name
  action                   = "lambda:InvokeFunction"
  principal                = "*"
  invoked_via_function_url = true
}

output "url" {
  description = "Function URL (sign requests with SigV4 when url_auth_type is AWS_IAM)."
  value       = aws_lambda_function_url.live.function_url
}
