#!/usr/bin/env bash
# Delete the pre-release images (tags starting with "0.") of the two GHCR packages.
#
#   tools/ghcr_cleanup.sh [--keep <version>] [--yes]
#
# It lists every version of ghcr.io/<owner>/s1d1-tantive and s1d1-tantive-base, prints what it would delete, asks
# for confirmation (unless --yes), then deletes. Nothing is deleted before the list has been printed.
#
# Deleted:  every version that carries at least one tag starting with "0." and no tag of the kept version,
#           plus every untagged version older than the oldest version that stays (minus a 24 h margin, because
#           the per-platform manifests of a multi-arch image are untagged and are created just before its index).
# Kept:     the --keep version (a tag equal to it or starting with "<version>-"), every other tag, and any
#           version created after the cutoff.
# Refused:  --keep <version> when no package has that version (a typo would delete everything), and any plan
#           that would delete a version carrying a kept tag (checked again just before deleting).
#
# The token needs the delete:packages scope:  gh auth refresh -s delete:packages
# Environment: GHCR_PACKAGES overrides the package list (space separated), GHCR_OWNER_PATH the API prefix
# (default /user, use /orgs/<org> for an organisation).
set -euo pipefail

keep=""
yes=0
while [ $# -gt 0 ]; do
  case "$1" in
    --keep) [ $# -ge 2 ] || { echo "--keep needs a version" >&2; exit 2; }; keep="$2"; shift 2 ;;
    --yes) yes=1; shift ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

packages="${GHCR_PACKAGES:-s1d1-tantive s1d1-tantive-base}"
owner_path="${GHCR_OWNER_PATH:-/user}"

command -v gh >/dev/null || { echo "gh is required" >&2; exit 2; }
command -v jq >/dev/null || { echo "jq is required" >&2; exit 2; }

# jq program: one line per version: <action>\t<id>\t<created_at>\t<tags>\t<reason>
plan_filter='
def tags: (.metadata.container.tags // []);
def is_kept: ($keep != "") and (tags | any(. == $keep or startswith($keep + "-")));
def is_pre: tags | any(startswith("0."));
.[]
| (if is_kept then ["keep", "kept version"]
   elif is_pre then ["delete", "pre-release tag"]
   elif (tags | length) > 0 then ["keep", "other tag"]
   elif ($cutoff == null) or (.created_at | fromdateiso8601) < $cutoff then ["delete", "untagged, older than what stays"]
   else ["keep", "untagged, newer than the cutoff"] end) as $a
| [$a[0], (.id | tostring), .created_at, (tags | join(",")), $a[1]] | @tsv'

# jq program: creation time of the oldest version that stays (all packages together), minus 24 h; null if none
cutoff_filter='
def tags: (.metadata.container.tags // []);
def is_kept: ($keep != "") and (tags | any(. == $keep or startswith($keep + "-")));
def is_pre: tags | any(startswith("0."));
[.[] | select(is_kept or ((tags | length) > 0 and (is_pre | not))) | .created_at | fromdateiso8601] | if length == 0 then null else min - 86400 end'

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

kept_found=0
for pkg in $packages; do
  gh api --paginate "$owner_path/packages/container/$pkg/versions" | jq -s 'add // []' > "$work/$pkg.json"
  if [ -n "$keep" ] && jq -e --arg keep "$keep" 'any(.[]; (.metadata.container.tags // []) | any(. == $keep or startswith($keep + "-")))' "$work/$pkg.json" >/dev/null; then
    kept_found=1
  fi
done

if [ -n "$keep" ] && [ "$kept_found" -eq 0 ]; then
  echo "refusing: no version tagged $keep (or $keep-*) in any of: $packages" >&2
  exit 2
fi

cutoff="$(jq -s --arg keep "$keep" "add | $cutoff_filter" "$work"/*.json)"

: > "$work/delete.tsv"
for pkg in $packages; do
  jq -r --arg keep "$keep" --argjson cutoff "$cutoff" "$plan_filter" "$work/$pkg.json" > "$work/$pkg.plan"
  echo "== $pkg: $(wc -l < "$work/$pkg.plan" | tr -d ' ') versions"
  while IFS=$'\t' read -r action id created tags reason; do
    printf '  %-7s %-12s %s  [%s]  %s\n' "$action" "$id" "$created" "${tags:-untagged}" "$reason"
    if [ "$action" = delete ]; then
      printf '%s\t%s\t%s\n' "$pkg" "$id" "$tags" >> "$work/delete.tsv"
    fi
  done < "$work/$pkg.plan"
done

# Last guard: nothing that carries a kept tag may be in the delete list.
if [ -n "$keep" ]; then
  while IFS=$'\t' read -r pkg id tags; do
    case ",$tags," in
      *",$keep,"*|*",$keep-"*) echo "refusing: version $id of $pkg carries the kept tag ($tags)" >&2; exit 3 ;;
    esac
  done < "$work/delete.tsv"
fi

count="$(wc -l < "$work/delete.tsv" | tr -d ' ')"
if [ "$count" -eq 0 ]; then
  echo "Nothing to delete."
  exit 0
fi

echo
echo "$count version(s) would be deleted (the token needs delete:packages: gh auth refresh -s delete:packages)."
if [ "$yes" -ne 1 ]; then
  printf 'Type "yes" to delete them: '
  read -r answer || answer=""
  if [ "$answer" != yes ]; then
    echo "Aborted, nothing deleted."
    exit 1
  fi
fi

while IFS=$'\t' read -r pkg id tags; do
  gh api -X DELETE "$owner_path/packages/container/$pkg/versions/$id" >/dev/null
  echo "deleted $pkg version $id (${tags:-untagged})"
done < "$work/delete.tsv"
