# Cost estimate

All figures are **us-east-1 list prices** and are estimates for architectural comparison,
not a quote. Check the [AWS Pricing Calculator](https://calculator.aws) for current rates.

Workload modelled: **1,000 images per month, ~2 MB each**, each producing three renditions
(thumbnail, medium, watermarked).

---

## What the Free Tier already covers

Several of these limits are **always free**, not just for the first twelve months:

| Service | Always-free allowance | This workload uses |
|---|---|---|
| AWS Lambda | 1M requests + 400,000 GB-seconds / month | ~6,000 requests, ~9,000 GB-s |
| Amazon SQS | 1M requests / month | ~2,000 |
| Amazon CloudFront | 1 TB egress + 10M requests / month | ~0.6 GB, ~5,000 |
| AWS Step Functions | 4,000 state transitions / month | ~10,000 |
| Amazon SNS | 1,000 email notifications / month | ~1,000 |
| Amazon DynamoDB | 25 GB storage | a few MB |

Twelve-month tier: S3 (5 GB, 20k GET, 2k PUT), API Gateway REST (1M calls).

**For a demo at this volume the only line that exceeds a free allowance is Step Functions**
(10,000 transitions against a 4,000 free allowance), costing about **$0.15**. Everything
else is $0.

---

## Cost beyond the Free Tier

### Step Functions — $0.25

Standard workflows bill **$0.025 per 1,000 state transitions**.

Transitions per image: `Validate` (1) + `GenerateVariants` parallel entry/exit (2) +
two `Resize` branches (2) + `Watermark` (1) + `StoreMetadata` (1) + `NotifySuccess` (1),
plus workflow start/end overhead ≈ **10**.

```
1,000 images × 10 transitions = 10,000 transitions
10,000 / 1,000 × $0.025 = $0.25
```

### Lambda — $0.15

Billed at **$0.0000166667 per GB-second** plus **$0.20 per 1M requests**.

| Function | Memory | Duration | GB-s per image |
|---|---|---|---|
| `presign` | 512 MB | ~0.1 s | 0.05 |
| `queue-consumer` | 512 MB | ~0.2 s | 0.10 |
| `validate` | 512 MB | ~1.0 s | 0.50 |
| `resize` ×2 | 1536 MB | ~2.0 s each | 6.00 |
| `watermark` | 1536 MB | ~1.5 s | 2.25 |
| `store` | 512 MB | ~0.15 s | 0.08 |
| **Total** | | | **≈ 9.0** |

```
1,000 × 9.0 GB-s = 9,000 GB-s × $0.0000166667 = $0.150
~6,000 requests × $0.20/1M                    = $0.001
```

The two `resize` invocations are ~70% of the compute bill. They get 1536 MB not because
they need the memory but because Lambda scales vCPU with memory, and Pillow is CPU-bound:
more memory finishes sooner, so the GB-second total barely moves while latency drops.

### CloudFront — $0.05

Renditions average ~200 KB; assume each image is viewed once across its three variants.

```
1,000 × 3 × 200 KB ≈ 600 MB × $0.085/GB = $0.051
```

Because renditions are immutable and cached for a year, repeat views are served from the
edge and add request cost only (~$0.01 per 10,000).

### S3 — $0.08

```
PUTs:    1,000 originals + 3,000 renditions = 4,000 × $0.005/1,000 = $0.020
Storage: 2 GB originals + 0.6 GB renditions = 2.6 GB × $0.023/GB   = $0.060
```

Originals expire after 30 days via lifecycle rule, so storage does not compound. Renditions
move to Intelligent-Tiering after 30 days (monitoring fee ~$0.0025 per 1,000 objects).

### Everything else — $0.03

| Service | Basis | Cost |
|---|---|---|
| API Gateway REST | ~6,000 calls (1 pre-sign + ~5 status polls each) × $3.50/M | $0.021 |
| SQS | 2,000 requests × $0.40/M | $0.001 |
| DynamoDB on-demand | ~3,000 writes, ~6,000 reads | $0.005 |
| SNS | 1,000 email notifications | $0.000 |
| CloudWatch Logs | ~50 MB ingested × $0.50/GB | $0.025 |

---

## Total

| Component | Cost per 1,000 images |
|---|---|
| Step Functions (Standard) | $0.250 |
| Lambda | $0.151 |
| S3 | $0.080 |
| CloudFront | $0.051 |
| CloudWatch Logs | $0.025 |
| API Gateway | $0.021 |
| DynamoDB, SQS, SNS | $0.006 |
| **Total** | **≈ $0.58** |

Roughly **$0.0006 per image**, or **$58 per 100,000 images per month**.

---

## Idle cost

With nothing being uploaded, the stack still costs:

- S3 storage for whatever is already there (~$0.023/GB/month)
- Intelligent-Tiering monitoring on renditions (~$0.0025 per 1,000 objects/month)
- CloudWatch Logs retention (14 days on the state machine log group)

Everything else — Lambda, Step Functions, API Gateway, SQS, DynamoDB on-demand,
CloudFront — bills purely per request and costs **$0 when idle**. There is no NAT Gateway
(~$32/month), no load balancer (~$16/month) and no RDS instance in this design, which is
the main reason it is so much cheaper than an EC2-based equivalent.

---

## The one optimisation worth calling out

Switching Step Functions from **Standard** to **Express** changes `Type: STANDARD` to
`Type: EXPRESS` in `template.yaml` and cuts the largest line item:

| | Standard | Express |
|---|---|---|
| Billing | $0.025 per 1,000 transitions | $1.00 per 1M requests + duration × memory |
| Cost per 1,000 images | $0.250 | ~$0.02 |
| Max duration | 1 year | 5 minutes |
| Execution history | Full, in the console for 90 days | CloudWatch Logs only |
| Delivery semantics | Exactly-once | At-least-once |

**Total would drop from ~$0.58 to ~$0.35 per 1,000 images.**

This project keeps Standard deliberately: the visual execution history is the point of a
learning project, the workflow is not high-volume, and at-least-once delivery would need
the resize step to become idempotent. At production volume the answer flips — which is
exactly the kind of trade-off the SAA exam asks about.
