from llm import get_llm

llm = get_llm()

prompt = """
Return ONLY valid JSON.

{
  "mapping": {
    "AWS AI/ML Engineer": [
      "Amazon Bedrock",
      "AWS"
    ]
  }
}
"""

import json

response = llm.invoke(prompt)

print(response.content)

json.loads(response.content)