#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP_ROOT="$(mktemp -d)"; trap 'rm -rf "$TMP_ROOT"' EXIT
for db in kraken nuc protein utility metaphlan magclass magfunction; do mkdir -p "$TMP_ROOT/$db"; done
touch "$TMP_ROOT/kraken/hash.k2d" "$TMP_ROOT/kraken/opts.k2d" "$TMP_ROOT/kraken/taxo.k2d"
touch "$TMP_ROOT/utility/map_ko_uniref90.txt.gz" "$TMP_ROOT/utility/map_level4ec_uniref90.txt.gz"
python3 - "$TMP_ROOT/registry.json" "$TMP_ROOT" <<'PY'
import json,sys
out,root=sys.argv[1:]
def entry(name,path,taxonomy="unclassified",sentinels=()):
 return {"database_name":name,"purpose":name,"path":f"{root}/{path}","release":"test-r1","taxonomy_system":taxonomy,"manifest_sha256":"test-sha","tool_compatibility_version":"test-v1","required_sentinel_files":list(sentinels)}
profile={"taxonomy_reads":{"kraken2":entry("kraken2","kraken","NCBI",("hash.k2d","opts.k2d","taxo.k2d")),"bracken":entry("bracken","kraken","NCBI")},"function_reads":{"humann_nucleotide":entry("nucleotide","nuc","NCBI"),"humann_protein":entry("protein","protein"),"humann_utility":entry("utility","utility"),"metaphlan":entry("metaphlan","metaphlan","NCBI"),"ko_mapping":entry("ko_mapping","utility",sentinels=("map_ko_uniref90.txt.gz",)),"ec_mapping":entry("ec_mapping","utility",sentinels=("map_level4ec_uniref90.txt.gz",))},"mag_annotation":{"classification":entry("classification","magclass","GTDB"),"function":entry("function","magfunction")}}
json.dump({"schema_version":1,"profiles":{"test":profile}},open(out,"w"),indent=2)
PY
python3 "$PROJECT_ROOT/workflow/bin/core/validate_databases.py" --registry "$TMP_ROOT/registry.json" --profile test --output "$TMP_ROOT/database.resolved.json" --enable-mags
python3 - "$TMP_ROOT/database.resolved.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
assert d["database_profile"] == "test"
assert d["databases"]["taxonomy_reads"]["kraken2"]["sentinel_validation"]["hash.k2d"] is True
assert d["databases"]["function_reads"]["ko_mapping"]["sentinel_validation"]["map_ko_uniref90.txt.gz"] is True
assert d["databases"]["function_reads"]["ec_mapping"]["sentinel_validation"]["map_level4ec_uniref90.txt.gz"] is True
assert d["resolved_manifest_sha256"]
PY
first_manifest_sha="$(sha256sum "$TMP_ROOT/database.resolved.json" | awk '{print $1}')"
first_manifest_mtime="$(stat -c %Y "$TMP_ROOT/database.resolved.json")"
sleep 1
python3 "$PROJECT_ROOT/workflow/bin/core/validate_databases.py" \
  --registry "$TMP_ROOT/registry.json" --profile test \
  --output "$TMP_ROOT/database.resolved.json" --enable-mags
second_manifest_sha="$(sha256sum "$TMP_ROOT/database.resolved.json" | awk '{print $1}')"
second_manifest_mtime="$(stat -c %Y "$TMP_ROOT/database.resolved.json")"
[[ "$first_manifest_sha" == "$second_manifest_sha" ]]
[[ "$first_manifest_mtime" == "$second_manifest_mtime" ]]

python3 - "$TMP_ROOT/registry.json" "$TMP_ROOT/changed-release.json" <<'PY'
import json, sys
d=json.load(open(sys.argv[1]))
d["profiles"]["test"]["function_reads"]["ko_mapping"]["manifest_sha256"] = "changed-mapping-sha"
json.dump(d,open(sys.argv[2],"w"),indent=2)
PY
python3 "$PROJECT_ROOT/workflow/bin/core/validate_databases.py" \
  --registry "$TMP_ROOT/changed-release.json" --profile test \
  --output "$TMP_ROOT/database.changed.json" --enable-mags
python3 - "$TMP_ROOT/database.resolved.json" "$TMP_ROOT/database.changed.json" <<'PY'
import json, sys
before=json.load(open(sys.argv[1]))
after=json.load(open(sys.argv[2]))
assert before["resolved_manifest_sha256"] != after["resolved_manifest_sha256"]
PY
if python3 "$PROJECT_ROOT/workflow/bin/core/validate_databases.py" --registry "$TMP_ROOT/registry.json" --profile missing --output "$TMP_ROOT/nope.json"; then
  echo "expected missing profile to fail" >&2; exit 1
fi
python3 - "$TMP_ROOT/registry.json" "$TMP_ROOT/missing-mapping.json" <<'PY'
import json, sys
d=json.load(open(sys.argv[1]))
d["profiles"]["test"]["function_reads"].pop("ko_mapping")
json.dump(d,open(sys.argv[2],"w"),indent=2)
PY
if python3 "$PROJECT_ROOT/workflow/bin/core/validate_databases.py" --registry "$TMP_ROOT/missing-mapping.json" --profile test --output "$TMP_ROOT/nope.json"; then
  echo "expected missing KO mapping to fail during preflight" >&2; exit 1
fi
rm "$TMP_ROOT/kraken/taxo.k2d"
if python3 "$PROJECT_ROOT/workflow/bin/core/validate_databases.py" --registry "$TMP_ROOT/registry.json" --profile test --output "$TMP_ROOT/nope.json"; then
  echo "expected missing sentinel to fail" >&2; exit 1
fi
printf 'database preflight tests passed\n'
