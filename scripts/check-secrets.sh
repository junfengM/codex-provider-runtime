#!/bin/sh
set -eu

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
project_root="$(dirname -- "$script_dir")"

for forbidden in .env auth.json config.toml models.json models_cache.json models-coexist.json; do
    if find "$project_root" -type f -name "$forbidden" -print -quit | grep -q .; then
        printf 'Forbidden local state file found: %s\n' "$forbidden" >&2
        exit 1
    fi
done

if rg -n --hidden \
    --glob '!.git/**' \
    --glob '!scripts/check-secrets.sh' \
    '(sk-[A-Za-z0-9_-]{20,}|gh[opsu]_[A-Za-z0-9]{20,}|DEEPSEEK_API_KEY=[^$<{[:space:]][^[:space:]]*)' \
    "$project_root"; then
    printf 'Possible credential material found\n' >&2
    exit 1
fi

printf 'Secret scan passed\n'
