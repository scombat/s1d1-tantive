"""A small corpus of real-looking text, written for these tests: English, French, code, Chinese, base64 and a mix.

`text(name, chars)` builds a text of exactly `chars` characters by cycling a language's paragraphs (each cycle starts
one paragraph later, so long texts do not repeat one block). Nothing is random: the same call always gives the same
text, which the recorded reference outputs (reference_0_8_1.json) rely on.
"""

from __future__ import annotations

import base64
import hashlib

ENGLISH = [
    "Hi team, the nightly export failed again around 02:40 UTC. The job log shows a timeout while uploading the "
    "third archive to the bucket, then a retry that never finished. Could someone check whether the network policy "
    "changed last week? We need the export before the quarterly review on Thursday, and the finance people are "
    "already asking where the numbers are.",
    "A hash table stores keys in buckets chosen by a hash function. When two keys land in the same bucket, the table "
    "resolves the collision either by chaining, where each bucket holds a small list, or by open addressing, where "
    "the key moves to the next free slot. The load factor decides when the table grows: past it, lookups slow down "
    "because chains get longer or probes get more frequent.",
    "The lighthouse keeper climbed the stairs for the last time that winter. Below him the harbour was empty, the "
    "boats pulled up on the shingle and turned over against the wind. He trimmed the wick, wiped the glass and "
    "waited for dusk, counting the seconds between the flashes of the far light across the bay, as he had every "
    "evening for thirty-one years.",
    "Meeting notes, Tuesday: we agreed to freeze the API until the migration is done. Maria will draft the "
    "deprecation notice, Tom owns the load test, and the database team will report on index bloat by Friday. Open "
    "question: do we keep the legacy endpoint for one more release, or remove it now and accept a few angry emails?",
    "To make the soup, soften two onions in butter, add a diced potato and three leeks cut into rings, then cover "
    "with stock and simmer for twenty minutes. Blend until smooth, season with salt, pepper and a little nutmeg, "
    "and finish with a spoon of cream. It keeps for three days in the fridge and freezes well.",
    "Why does the median resist outliers better than the mean? Suppose a sample of salaries where one person earns "
    "a hundred times more than the others. The mean moves towards that value in proportion to it, while the median "
    "only moves if the outlier changes which observation sits in the middle, which it does not.",
]

FRENCH = [
    "Bonjour, je n'arrive plus à me connecter au portail depuis la mise à jour de lundi. Le mot de passe est "
    "accepté, puis la page reste blanche et le navigateur affiche une erreur de certificat. J'ai essayé avec Firefox "
    "et Chrome, en navigation privée aussi, sans succès. Pouvez-vous regarder rapidement ? Je dois déposer mon "
    "dossier avant vendredi midi.",
    "Une file d'attente sépare celui qui produit le travail de celui qui le traite. Quand la file se remplit plus "
    "vite qu'elle ne se vide, le temps d'attente croît sans limite : c'est pourquoi un service sous charge doit "
    "refuser tôt plutôt que répondre tard. Un refus rapide coûte une nouvelle tentative, une réponse tardive coûte "
    "souvent bien davantage.",
    "Le village dormait encore quand la boulangère ouvrit ses volets. L'odeur du pain chaud descendait la rue "
    "jusqu'à la fontaine, où deux chats se disputaient une flaque de soleil. Au loin, le car de six heures "
    "klaxonna deux fois, comme chaque matin, pour prévenir les écoliers en retard qu'il n'attendrait pas.",
    "Compte rendu du comité du 12 : le budget de formation est reconduit à l'identique, la refonte du site est "
    "repoussée au second semestre et le prestataire de nettoyage sera remplacé à l'échéance du contrat. Point "
    "ouvert : faut-il garder deux jours de télétravail par semaine ou passer à trois pour les équipes support ?",
    "Pour réussir une pâte brisée, mélangez du bout des doigts 250 g de farine et 125 g de beurre froid coupé en "
    "dés, ajoutez une pincée de sel puis un jaune d'œuf et un peu d'eau glacée. Ne travaillez pas trop la pâte : "
    "formez une boule, filmez-la et laissez-la reposer une heure au frais avant de l'étaler.",
    "Pourquoi le ciel est-il bleu ? La lumière du soleil contient toutes les couleurs, mais les molécules de l'air "
    "diffusent beaucoup plus les courtes longueurs d'onde que les longues. Le bleu est donc renvoyé dans toutes les "
    "directions, et c'est lui que l'on voit en regardant ailleurs que vers le soleil.",
]

CODE = [
    "def moving_average(values: list[float], window: int) -> list[float]:\n"
    '    """The mean of each run of `window` consecutive values."""\n'
    "    if window < 1:\n"
    '        raise ValueError("window must be at least 1")\n'
    "    total, out = 0.0, []\n"
    "    for i, v in enumerate(values):\n"
    "        total += v\n"
    "        if i >= window:\n"
    "            total -= values[i - window]\n"
    "        if i >= window - 1:\n"
    "            out.append(total / window)\n"
    "    return out\n",
    "class LRUCache:\n"
    "    def __init__(self, capacity: int) -> None:\n"
    "        self.capacity = capacity\n"
    "        self.items: OrderedDict[str, bytes] = OrderedDict()\n"
    "\n"
    "    def get(self, key: str) -> bytes | None:\n"
    "        if key not in self.items:\n"
    "            return None\n"
    "        self.items.move_to_end(key)\n"
    "        return self.items[key]\n"
    "\n"
    "    def put(self, key: str, value: bytes) -> None:\n"
    "        self.items[key] = value\n"
    "        self.items.move_to_end(key)\n"
    "        while len(self.items) > self.capacity:\n"
    "            self.items.popitem(last=False)\n",
    "async def fetch_all(urls: list[str], limit: int = 8) -> dict[str, int]:\n"
    "    semaphore = asyncio.Semaphore(limit)\n"
    "    async with httpx.AsyncClient(timeout=10) as client:\n"
    "\n"
    "        async def one(url: str) -> tuple[str, int]:\n"
    "            async with semaphore:\n"
    "                response = await client.get(url)\n"
    "                return url, response.status_code\n"
    "\n"
    "        pairs = await asyncio.gather(*(one(u) for u in urls))\n"
    "    return dict(pairs)\n",
    "SELECT c.name, COUNT(o.id) AS orders, SUM(o.total_cents) / 100.0 AS revenue\n"
    "FROM customers AS c\n"
    "LEFT JOIN orders AS o ON o.customer_id = c.id AND o.created_at >= DATE '2026-01-01'\n"
    "GROUP BY c.name\n"
    "HAVING COUNT(o.id) > 3\n"
    "ORDER BY revenue DESC\n"
    "LIMIT 20;\n",
    "fn parse_header(line: &str) -> Option<(&str, &str)> {\n"
    "    let (name, value) = line.split_once(':')?;\n"
    "    let name = name.trim();\n"
    "    if name.is_empty() || name.contains(' ') {\n"
    "        return None;\n"
    "    }\n"
    "    Some((name, value.trim()))\n"
    "}\n",
    "#!/usr/bin/env bash\n"
    "set -euo pipefail\n"
    'for f in "$@"; do\n'
    '  if [[ ! -s "$f" ]]; then\n'
    '    echo "skip empty $f" >&2\n'
    "    continue\n"
    "  fi\n"
    '  gzip -9 --keep "$f" && sha256sum "$f.gz" >> checksums.txt\n'
    "done\n",
]

CHINESE = [
    "您好，我昨天在网上订了一台洗衣机，订单显示已经发货，但是物流信息三天都没有更新。请帮我查一下包裹现在在哪里，"
    "如果找不到的话，能不能重新发一台或者退款？我下周要搬家，希望在那之前能收到。",
    "排队系统把产生任务的一方和处理任务的一方分开。当任务到达的速度超过处理的速度时，等待时间会不断增长，"
    "所以一个在高负载下运行的服务应该尽早拒绝请求，而不是很晚才给出回答。快速拒绝只需要重试一次。",
    "清晨的小镇还在沉睡，面包店的老板娘已经打开了窗户。新鲜面包的香味顺着街道一直飘到广场的喷泉边，"
    "两只猫正在那里争抢一块阳光。远处，六点钟的公交车按了两下喇叭，提醒迟到的学生它不会再等。",
    "会议纪要：培训预算保持不变，网站改版推迟到下半年，清洁服务合同到期后更换供应商。待定问题：支持团队"
    "每周是保留两天远程办公，还是改为三天？请各部门在周五之前提交意见。",
    "做番茄炒蛋时，先把鸡蛋打散，加少许盐，用热油快速炒到半熟后盛出。再放番茄块翻炒出汁，加一点糖调味，"
    "最后把鸡蛋倒回锅里，翻炒均匀，撒上葱花就可以出锅了。",
    "为什么天空是蓝色的？太阳光包含各种颜色，但空气中的分子对短波长的光散射得比长波长的光多得多。"
    "因此蓝光被散射到各个方向，我们看向天空的任何地方都能看到它。",
]

MIXED = [
    "Release 2.4.1 (2026-10-30): fixed the crash in `parse_header` when a line has no colon; p99 latency down from "
    "412 ms to 287 ms on the 8-vCPU runner 🚀. Voir aussi https://example.org/changelog#v2-4-1 pour le détail.",
    "| region | requests | errors | ratio |\n|---|---:|---:|---:|\n| eu-west | 1 204 331 | 2 117 | 0.18 % |\n"
    "| us-east | 987 004 | 4 902 | 0.50 % |\n| ap-south | 312 776 | 88 | 0.03 % |\n",
    '{"id": "ord_7f3a", "items": [{"sku": "A-1001", "qty": 2, "price": 19.9}, {"sku": "B-2002", "qty": 1, '
    '"price": 249.0}], "customer": {"name": "李雷", "city": "Lyon"}, "notes": "Livrer après 18h, merci 🙏"}',
    "Traceback (most recent call last):\n  File \"/app/worker.py\", line 88, in run\n    result = step(payload)\n"
    "  File \"/app/steps.py\", line 41, in step\n    return payload['items'][0]['qty'] * price\nKeyError: 'items'\n",
    "Can you translate this for the Shanghai office? 我们下周二上午十点开会，讨论第三季度的预算。Merci de confirmer "
    "votre présence avant lundi. Agenda: 1) budget Q3, 2) hiring plan, 3) 其他事项.",
    "Formula check: E = m·c², with c ≈ 2.998 × 10⁸ m/s, so 1 g of matter ≈ 8.99 × 10¹³ J ≈ 21.5 kt TNT. "
    "Est-ce que l'ordre de grandeur est bon ? 计算结果看起来合理。",
]

PARAGRAPHS = {"english": ENGLISH, "french": FRENCH, "code": CODE, "chinese": CHINESE, "mixed": MIXED}
NAMES = ("english", "french", "code", "chinese", "base64", "mixed")


def _base64(chars: int) -> str:
    """Wrapped base64 (76 characters per line, as in MIME) of a SHA-256 chain: looks random, is reproducible."""
    raw, block = bytearray(), hashlib.sha256(b"tantive corpus").digest()
    while len(raw) * 4 // 3 < chars + 64:
        raw += block
        block = hashlib.sha256(block).digest()
    flat = base64.b64encode(bytes(raw)).decode("ascii")
    lines = [flat[i : i + 76] for i in range(0, len(flat), 76)]
    return "\n".join(lines)[:chars]


def text(name: str, chars: int) -> str:
    """Exactly `chars` characters of the named language."""
    if name == "base64":
        return _base64(chars)
    paragraphs = PARAGRAPHS[name]
    parts, length, cycle = [], 0, 0
    while length < chars:
        for i in range(len(paragraphs)):
            p = paragraphs[(i + cycle) % len(paragraphs)]
            parts.append(p)
            length += len(p) + 2
        cycle += 1
    return "\n\n".join(parts)[:chars]
