#!/usr/bin/env bash
#
# coexist.sh - 让 Codex 同时保留 ChatGPT 官方模型与自定义 provider（如 DeepSeek）
#
# 用法:
#   coexist.sh status                查看当前模型/provider/目录状态
#   coexist.sh keychain-set          在 macOS Keychain 中安全保存 DeepSeek API Key
#   coexist.sh enable-both           恢复 ChatGPT 默认，同时保留自定义模型可选
#   coexist.sh enable-deepseek       将默认模型切到自定义目录中的第一个模型
#   coexist.sh set-default-deepseek  enable-deepseek 的兼容别名
#   coexist.sh set-model <模型> [provider]  设置默认模型（可选指定 provider）
#   coexist.sh merge                 合并官方与自定义模型目录
#   coexist.sh refresh               刷新官方与自定义模型的合并目录
#   coexist.sh backup                备份当前 config.toml / models.json
#   coexist.sh restore [备份文件]     从备份还原 config.toml
#   coexist.sh validate              用 codex debug models / doctor 校验
#   coexist.sh history [all|provider] 审计本地线程 provider
#   coexist.sh test-deepseek         显式请求 DeepSeek provider
#
set -euo pipefail

CODEX_DIR="${COEXIST_CODEX_HOME:-${CODEX_HOME:-$HOME/.codex}}"
CONFIG="$CODEX_DIR/config.toml"
CACHE="$CODEX_DIR/models_cache.json"
CUSTOM="$CODEX_DIR/models.json"
COEXIST="$CODEX_DIR/models-coexist.json"
BACKUP_DIR="$CODEX_DIR/backup-coexist"
DEEPSEEK_BASE_URL="${DEEPSEEK_BASE_URL:-https://api.deepseek.com}"
DEEPSEEK_KEYCHAIN_SERVICE="${DEEPSEEK_KEYCHAIN_SERVICE:-com.openai.codex.deepseek-api-key}"
DEEPSEEK_KEYCHAIN_ACCOUNT="${DEEPSEEK_KEYCHAIN_ACCOUNT:-$(id -un)}"

REMOVE="__TOML_REMOVE__"

find_codex() {
  if [ -n "${CODEX_CLI_PATH:-}" ] && [ -x "$CODEX_CLI_PATH" ]; then
    printf '%s\n' "$CODEX_CLI_PATH"
    return
  fi
  if command -v codex >/dev/null 2>&1; then
    command -v codex
    return
  fi
  if [ -x "/Applications/ChatGPT.app/Contents/Resources/codex" ]; then
    printf '%s\n' "/Applications/ChatGPT.app/Contents/Resources/codex"
    return
  fi
  printf '\n'
}
CODEX_BIN="$(find_codex)"

die() { printf '错误: %s\n' "$*" >&2; exit 1; }
info() { printf '%s\n' "$*"; }

require_config() {
  [ -f "$CONFIG" ] || die "未找到 $CONFIG"
}

toml_quote() {
  printf '"%s"' "$1"
}

# toml_set <key> <TOML value> | <key> __TOML_REMOVE__
# 只处理顶层键：在原位置替换；若不存在则在第一个表头前插入；REMOVE 则删除该键。
toml_set() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  awk -v key="$key" -v value="$value" '
    BEGIN { seen = 0; in_header = 0 }
    /^\[/ {
      if (!in_header && !seen && value != "'"$REMOVE"'") {
        print key " = " value
        seen = 1
      }
      in_header = 1
      print
      next
    }
    !in_header && $0 ~ "^" key "[[:space:]]*=" {
      if (value != "'"$REMOVE"'") {
        print key " = " value
        seen = 1
      }
      next
    }
    { print }
    END {
      if (!in_header && !seen && value != "'"$REMOVE"'") print key " = " value
    }
  ' "$CONFIG" > "$tmp"
  mv "$tmp" "$CONFIG"
}

config_get() {
  local key="$1"
  awk -v key="$key" '
    !in_header && $0 ~ "^" key "[[:space:]]*=" {
      sub(/^[^=]*=[[:space:]]*/, "")
      gsub(/^"|"$/, "")
      print
      exit
    }
    /^\[/ { in_header = 1 }
  ' "$CONFIG"
}

provider_names() {
  awk '
    /^\[model_providers\.[^.]+\]$/ {
      sub(/^\[model_providers\./, ""); sub(/\]$/, "")
      print
    }
  ' "$CONFIG"
}

has_deepseek_provider() {
  grep -q '^\[model_providers\.deepseek\]$' "$CONFIG"
}

keychain_has_deepseek_key() {
  [ "$(uname -s)" = "Darwin" ] || return 1
  command -v security >/dev/null 2>&1 || return 1
  security find-generic-password \
    -a "$DEEPSEEK_KEYCHAIN_ACCOUNT" \
    -s "$DEEPSEEK_KEYCHAIN_SERVICE" \
    -w >/dev/null 2>&1
}

keychain_set() {
  [ "$(uname -s)" = "Darwin" ] || die "keychain-set 仅支持 macOS"
  command -v security >/dev/null 2>&1 || die "未找到 security 命令"
  info "请输入 DeepSeek API Key；输入不会回显。"
  security add-generic-password \
    -U \
    -a "$DEEPSEEK_KEYCHAIN_ACCOUNT" \
    -s "$DEEPSEEK_KEYCHAIN_SERVICE" \
    -l "Codex DeepSeek API Key" \
    -T /usr/bin/security \
    -w
  keychain_has_deepseek_key || die "Keychain 写入后无法读取 DeepSeek API Key"
  info "DeepSeek API Key 已安全保存到 macOS Keychain。"
}

ensure_deepseek_provider() {
  require_config
  has_deepseek_provider && return

  local tmp
  tmp="$(mktemp)"
  cp "$CONFIG" "$tmp"

  if keychain_has_deepseek_key; then
    cat >> "$tmp" <<EOF

[model_providers.deepseek]
name = "DeepSeek"
base_url = "$DEEPSEEK_BASE_URL"
wire_api = "responses"

[model_providers.deepseek.auth]
command = "/usr/bin/security"
args = ["find-generic-password", "-a", "$DEEPSEEK_KEYCHAIN_ACCOUNT", "-s", "$DEEPSEEK_KEYCHAIN_SERVICE", "-w"]
timeout_ms = 5000
refresh_interval_ms = 300000
EOF
  elif [ -n "${DEEPSEEK_API_KEY:-}" ]; then
    cat >> "$tmp" <<EOF

[model_providers.deepseek]
name = "DeepSeek"
base_url = "$DEEPSEEK_BASE_URL"
wire_api = "responses"
env_key = "DEEPSEEK_API_KEY"
env_key_instructions = "Set DEEPSEEK_API_KEY before starting Codex."
EOF
  else
    rm -f "$tmp"
    die "未找到 DeepSeek 凭据；macOS 先运行 keychain-set，其他系统设置 DEEPSEEK_API_KEY"
  fi

  mv "$tmp" "$CONFIG"
  chmod 0600 "$CONFIG"
  info "已注册 DeepSeek provider（密钥未写入 config.toml）。"
}

derive_deepseek_catalog() {
  [ -f "$CACHE" ] || die "缺少官方模型缓存 $CACHE (先运行一次 Codex 生成)"
  command -v jq >/dev/null 2>&1 || die "未找到 jq"

  local derived merged
  derived="$(mktemp)"
  merged="$(mktemp)"
  jq '
    first(.models[] | select(.visibility == "list" or .visibility == null)) as $base
    | {
        models: [
          ($base
            | .slug = "deepseek-v4-flash"
            | .display_name = "DeepSeek V4 Flash"
            | .description = "Fast, economical DeepSeek V4 model for agentic coding."
            | .default_reasoning_level = "high"
            | .supported_reasoning_levels = [
                {effort: "high", description: "Deep reasoning with lower latency"},
                {effort: "max", description: "Maximum reasoning depth"}
              ]
            | .priority = 20
            | .additional_speed_tiers = []
            | .service_tiers = []
            | .support_verbosity = false
            | .supports_image_detail_original = false
            | .supports_search_tool = false
            | .input_modalities = ["text"]
            | .context_window = 1000000
            | .max_context_window = 1000000),
          ($base
            | .slug = "deepseek-v4-pro"
            | .display_name = "DeepSeek V4 Pro"
            | .description = "Frontier DeepSeek V4 model for complex agentic coding."
            | .default_reasoning_level = "high"
            | .supported_reasoning_levels = [
                {effort: "high", description: "Deep reasoning with lower latency"},
                {effort: "max", description: "Maximum reasoning depth"}
              ]
            | .priority = 21
            | .additional_speed_tiers = []
            | .service_tiers = []
            | .support_verbosity = false
            | .supports_image_detail_original = false
            | .supports_search_tool = false
            | .input_modalities = ["text"]
            | .context_window = 1000000
            | .max_context_window = 1000000)
        ]
      }
  ' "$CACHE" > "$derived"

  if [ -f "$CUSTOM" ]; then
    jq -n --slurpfile existing "$CUSTOM" --slurpfile derived "$derived" '
      (($derived[0].models // []) | map({key: .slug, value: .}) | from_entries) as $generated
      | (($existing[0].models // []) | map({key: .slug, value: .}) | from_entries) as $current
      | {models: (($generated + $current) | to_entries | map(.value))}
    ' > "$merged"
  else
    cp "$derived" "$merged"
  fi

  jq -e '
    (.models | length > 0)
    and any(.models[]; .slug == "deepseek-v4-flash")
    and any(.models[]; .slug == "deepseek-v4-pro")
  ' "$merged" >/dev/null || die "无法生成完整的 DeepSeek V4 模型目录"
  install -m 0600 "$merged" "$CUSTOM"
  rm -f "$derived" "$merged"
  info "已准备 DeepSeek 模型目录 ${CUSTOM}。"
}

backup() {
  require_config
  mkdir -p "$BACKUP_DIR"
  local ts
  ts="$(date +%Y%m%d-%H%M%S)"
  cp "$CONFIG" "$BACKUP_DIR/config.$ts.toml"
  [ -f "$CUSTOM" ] && cp "$CUSTOM" "$BACKUP_DIR/models.$ts.json"
  [ -f "$COEXIST" ] && cp "$COEXIST" "$BACKUP_DIR/models-coexist.$ts.json"
  info "已备份到 $BACKUP_DIR (${ts})"
}

merge() {
  require_config
  [ -f "$CACHE" ] || die "缺少官方模型缓存 $CACHE (先运行一次 Codex 生成)"
  [ -f "$CUSTOM" ] || die "缺少自定义模型目录 $CUSTOM"
  local tmp
  tmp="$(mktemp)"
  jq -n --slurpfile a "$CACHE" --slurpfile b "$CUSTOM" \
    '{models: ((($a[0].models // []) + ($b[0].models // [])) | unique_by(.slug))}' \
    > "$tmp"
  local n
  n="$(jq '.models | length' "$tmp")"
  [ "$n" -gt 0 ] || die "合并目录为空"
  install -m 0600 "$tmp" "$COEXIST"
  rm -f "$tmp"
  info "已生成合并目录 $COEXIST ($n 个模型)"
}

ensure_both_catalog() {
  derive_deepseek_catalog
  merge
  toml_set model_catalog_json "$(toml_quote "$COEXIST")"
}

official_default_model() {
  if [ -f "$CACHE" ]; then
    jq -r '.models[] | select(.visibility == "list" or .visibility == null) | .slug' "$CACHE" \
      | head -1
  fi
}

enable_both() {
  require_config
  backup
  ensure_deepseek_provider
  ensure_both_catalog
  local default
  default="$(official_default_model)"
  default="${default:-gpt-5.6-sol}"
  toml_set model "$(toml_quote "$default")"
  toml_set model_reasoning_effort '"medium"'
  toml_set model_provider "$REMOVE"
  toml_set preferred_auth_method "$REMOVE"
  toml_set forced_login_method "$REMOVE"
  info "已恢复 ChatGPT 默认 ($default)，自定义模型保留在合并目录中。"
  info "重启 Codex 后，模型选择器应同时显示官方与自定义模型。"
}

refresh() {
  require_config
  backup
  ensure_deepseek_provider
  ensure_both_catalog
  info "已刷新 ChatGPT 与 DeepSeek 的合并模型目录。"
}

enable_deepseek() {
  require_config
  local first_model
  first_model="$(jq -r '.models[0].slug' "$CUSTOM" 2>/dev/null || true)"
  if [ -z "$first_model" ]; then
    die "自定义目录 $CUSTOM 中未找到模型"
  fi
  set_model "$first_model" "${1:-}"
}

set_model() {
  require_config
  local model="$1" provider="${2:-}" providers
  [ -n "$model" ] || die "缺少模型名"
  backup
  ensure_both_catalog
  toml_set model "$(toml_quote "$model")"
  if [ -n "$provider" ]; then
    toml_set model_provider "$(toml_quote "$provider")"
  else
    providers="$(provider_names)"
    if [ -n "$providers" ] && [ "$(printf '%s\n' "$providers" | wc -l | tr -d ' ')" -eq 1 ]; then
      toml_set model_provider "$(toml_quote "$providers")"
    else
      toml_set model_provider "$REMOVE"
    fi
  fi
  info "默认模型已设为 $model${provider:+ (provider: $provider)}"
}

restore() {
  require_config
  local target="${1:-}"
  if [ -z "$target" ]; then
    target="$(ls -t "$BACKUP_DIR"/config.*.toml 2>/dev/null | head -1 || true)"
  fi
  [ -n "$target" ] && [ -f "$target" ] || die "未找到可用备份"
  backup
  cp "$target" "$CONFIG"
  info "已从 $target 还原 config.toml"
}

status() {
  require_config
  info "config: $CONFIG"
  info "model      = $(config_get model)"
  info "provider   = $(config_get model_provider)"
  info "catalog    = $(config_get model_catalog_json)"
  info "forced_login_method = $(config_get forced_login_method)"
  info "providers  = $(provider_names | tr '\n' ' ')"
  if [ -n "$CODEX_BIN" ]; then
    info "加载的模型（codex debug models）:"
    "$CODEX_BIN" debug models 2>/dev/null | jq -r '.models[].slug' | sed 's/^/  /'
  else
    info "未找到 codex CLI，跳过模型列表检查"
  fi
}

validate() {
  require_config
  [ -n "$CODEX_BIN" ] || die "未找到 codex CLI"
  has_deepseek_provider || die "config.toml 中未注册 DeepSeek provider"
  [ -z "$(config_get forced_login_method)" ] || die "forced_login_method 会绕过 ChatGPT 登录"
  local models
  info "== codex debug models =="
  models="$("$CODEX_BIN" debug models 2>/dev/null)"
  printf '%s\n' "$models" | jq -e 'any(.models[]; .slug | startswith("gpt-"))' >/dev/null \
    || die "加载目录中缺少 GPT 模型"
  printf '%s\n' "$models" | jq -e 'any(.models[]; .slug | startswith("deepseek-"))' >/dev/null \
    || die "加载目录中缺少 DeepSeek 模型"
  printf '%s\n' "$models" | jq -r '.models | length as $n | "共 \($n) 个模型:", .[].slug'
  info "== codex doctor =="
  "$CODEX_BIN" doctor --summary --no-color 2>&1 | tail -n 20 || true
  info "ChatGPT/DeepSeek 结构校验通过。"
}

history() {
  local provider="${1:-all}" db where
  db="$(ls -t "$CODEX_DIR"/state_*.sqlite 2>/dev/null | head -1 || true)"
  [ -n "$db" ] && [ -f "$db" ] || die "未找到 $CODEX_DIR/state_*.sqlite"
  command -v sqlite3 >/dev/null 2>&1 || die "未找到 sqlite3"

  case "$provider" in
    all) where="1=1" ;;
    openai|deepseek) where="model_provider = '$provider'" ;;
    *) die "provider 只支持 all、openai 或 deepseek" ;;
  esac

  sqlite3 -readonly -header -column "$db" "
    SELECT
      id,
      datetime(created_at, 'unixepoch', 'localtime') AS created_local,
      model_provider,
      model,
      reasoning_effort,
      cwd,
      substr(title, 1, 60) AS title
    FROM threads
    WHERE $where
    ORDER BY created_at DESC, id DESC;
  "
}

test_deepseek() {
  [ -n "$CODEX_BIN" ] || die "未找到 codex CLI"
  local output
  if ! output="$(
    "$CODEX_BIN" exec \
      --ephemeral \
      --skip-git-repo-check \
      -C "$PWD" \
      -s read-only \
      -c 'model_provider="deepseek"' \
      -c 'model_reasoning_effort="high"' \
      -m deepseek-v4-flash \
      'Reply with exactly: DEEPSEEK_ROUTE_OK' 2>&1
  )"; then
    printf '%s\n' "$output" >&2
    die "DeepSeek 显式路由测试失败"
  fi
  printf '%s\n' "$output"
  printf '%s\n' "$output" | grep -q 'DEEPSEEK_ROUTE_OK' \
    || die "响应中缺少 DEEPSEEK_ROUTE_OK"
  info "DeepSeek 显式路由测试通过。"
}

case "${1:-}" in
  status) status ;;
  keychain-set) keychain_set ;;
  enable-both) enable_both ;;
  enable-deepseek) enable_deepseek "${2:-}" ;;
  set-default-deepseek) enable_deepseek "${2:-deepseek}" ;;
  set-model) [ $# -ge 2 ] && set_model "$2" "${3:-}" || { echo "用法: coexist.sh set-model <模型> [provider]"; exit 1; } ;;
  merge) merge ;;
  refresh) refresh ;;
  backup) backup ;;
  restore) restore "${2:-}" ;;
  validate) validate ;;
  history) history "${2:-all}" ;;
  test-deepseek) test_deepseek ;;
  *) sed -n '2,18p' "$0" ;;
esac
