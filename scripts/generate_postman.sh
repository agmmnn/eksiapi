#!/usr/bin/env bash

set -euo pipefail

repository_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$repository_dir"
npx -y openapi-to-postmanv2@6.3.2 \
  --spec openapi.yaml \
  --output postman/eksi-sozluk-api.postman_collection.json \
  --pretty \
  --options-config postman/converter-options.json

python3 scripts/postprocess_postman.py postman/eksi-sozluk-api.postman_collection.json
