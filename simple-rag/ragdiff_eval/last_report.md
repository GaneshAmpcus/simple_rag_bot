# RagDiff report

**Verdict:** fail

## Attribution

**Culprit:** retrieval (tier 1, confidence 1.00)

**Suggested fix:** Revert `chunk_size` (800 -> 200), `chunk_overlap` (160 -> 40), `top_k` (3 -> 1), or raise `top_k` so the chunk holding the answer is still retrieved.

- 3 of 3 regressed question(s) retrieved different contexts
- How does AWS Lambda integrate with Amazon Bedrock for AI workflows? lost: "(Skip)One thing to watch out for — if an agent is making more than 10 model calls for one request, something's gone wrong. Set a CloudWatch alarm for that. It's"
- What is the rol of Amazon DynamoDB in the AWS ecosystem integations with Bedrock, especaily in handeling conversaton memory? lost: "(Skip)One thing to watch out for — if an agent is making more than 10 model calls for one request, something's gone wrong. Set a CloudWatch alarm for that. It's"
- What is the rol of Amazon Comprehend in the AWS ecosystem with Bedrock? lost: "(Skip)One thing to watch out for — if an agent is making more than 10 model calls for one request, something's gone wrong. Set a CloudWatch alarm for that. It's"

| Metric | Base mean | Head mean | Delta | 95% CI | Regressed |
| --- | ---: | ---: | ---: | --- | ---: |
| context_recall | 1.0000 | 0.0000 | -1.0000 | [-1.0000, -1.0000] | 3 |
| faithfulness | 0.9524 | 0.0000 | -0.9524 | [-1.1573, -0.7475] | 3 |

## Regressed questions: context_recall

- How does AWS Lambda integrate with Amazon Bedrock for AI workflows? (1.000 -> 0.000, -1.000)
- What is the rol of Amazon DynamoDB in the AWS ecosystem integations with Bedrock, especaily in handeling conversaton memory? (1.000 -> 0.000, -1.000)
- What is the rol of Amazon Comprehend in the AWS ecosystem with Bedrock? (1.000 -> 0.000, -1.000)

## Regressed questions: faithfulness

- How does AWS Lambda integrate with Amazon Bedrock for AI workflows? (1.000 -> 0.000, -1.000)
- What is the rol of Amazon DynamoDB in the AWS ecosystem integations with Bedrock, especaily in handeling conversaton memory? (1.000 -> 0.000, -1.000)
- What is the rol of Amazon Comprehend in the AWS ecosystem with Bedrock? (0.857 -> 0.000, -0.857)
