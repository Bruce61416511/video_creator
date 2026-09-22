import hmac
import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

CONFIG_DIR = Path(__file__).parent.parent / "config"
ENV_PATH = Path(__file__).parent.parent / ".env"

router = APIRouter(prefix="/config")

# 管理令牌：设了之后，远程改 API Key 需要在请求头带 X-Admin-Token。
# 不设则退化为「只允许服务器本机修改」。
ADMIN_TOKEN = (os.environ.get("ADMIN_TOKEN") or "").strip()

_TRUSTED_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _assert_can_write_secrets(request: Request) -> None:
    """
    守卫「写 API Key」这一类高危操作。

    背景：这个接口会把传入的值直接写进服务器磁盘上的 .env 文件。
    没有守卫时，任何能打开页面的人都可以把 Key 换成自己的、或者直接清空，
    导致服务不可用。注意这不是「访问控制」—— 普通配置照常开放，只拦这一件事。
    """
    if not ADMIN_TOKEN:
        host = (request.client.host if request.client else "") or ""
        if host in _TRUSTED_HOSTS:
            return
        raise HTTPException(
            status_code=403,
            detail=(
                "出于安全考虑，API Key 只允许在服务器本机修改。"
                "如需远程修改，请在服务器环境变量里设置 ADMIN_TOKEN，"
                "并在请求头带上 X-Admin-Token。"
            ),
        )

    supplied = request.headers.get("X-Admin-Token", "")
    # 恒定时间比较，避免通过响应耗时逐位猜令牌
    if not hmac.compare_digest(supplied, ADMIN_TOKEN):
        raise HTTPException(status_code=403, detail="管理令牌不正确")


def _read_json(filename: str) -> dict:
    filepath = CONFIG_DIR / filename
    if not filepath.exists():
        return {}
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(filename: str, data: dict):
    filepath = CONFIG_DIR / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _mask(key: str) -> str:
    """key 脱敏：前 6 + *** + 后 4"""
    if not key:
        return ""
    if len(key) <= 12:
        return key[:2] + "***"
    return key[:6] + "***" + key[-4:]


def _load_env_file() -> dict:
    """读 .env 文件，返回 dict（KEY=VALUE）"""
    result = {}
    if not ENV_PATH.exists():
        return result
    with open(ENV_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def _upsert_env(key: str, value: str):
    """把 KEY=VALUE 写入 .env（覆盖已有同名行，没有就追加）"""
    lines = []
    found = False
    if ENV_PATH.exists():
        with open(ENV_PATH, encoding="utf-8") as f:
            lines = f.readlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('#') or '=' not in stripped:
            continue
        k, _, _ = stripped.partition('=')
        if k.strip() == key:
            lines[i] = f"{key}={value}\n"
            found = True
            break
    if not found:
        if lines and not lines[-1].endswith('\n'):
            lines.append('\n')
        lines.append(f"{key}={value}\n")
    with open(ENV_PATH, 'w', encoding="utf-8") as f:
        f.writelines(lines)
    # 同步到当前进程的环境变量，让 key_manager 立即生效
    os.environ[key] = value


def _is_masked(value: str) -> bool:
    """判断前端回传的值是不是 mask 后的占位符"""
    return bool(value) and "***" in value


# ========== 模型配置 ==========

SENSITIVE_KEYS = ("api_key", "llm_api_key")


@router.get("/models")
async def get_models():
    from services.key_manager import get_dashscope_api_key, get_llm_api_key
    data = _read_json("models.json")
    # 优先展示环境变量里的真实 key（mask 后），fallback 到 models.json 里的历史值
    real_dash = get_dashscope_api_key() or data.get("api_key", "")
    real_llm = get_llm_api_key() or data.get("llm_api_key", "")
    data["api_key"] = _mask(real_dash)
    data["llm_api_key"] = _mask(real_llm)
    return data


@router.put("/models")
async def update_models(data: dict, request: Request):
    # 守卫放在 try 之外：它抛的是 HTTPException(403)，若包在 try 里会被下面的
    # except Exception 吞掉并重新包装成 500，把「没权限」变成含糊的「服务器错误」
    if any(
        data.get(f) and not _is_masked(data.get(f, ""))
        for f in SENSITIVE_KEYS
    ):
        _assert_can_write_secrets(request)

    try:
        # 把敏感 key 单独写到 .env，不进 models.json
        for field in SENSITIVE_KEYS:
            if field in data:
                new_val = data.get(field, "")
                if new_val and not _is_masked(new_val):
                    env_name = "DASHSCOPE_API_KEY" if field == "api_key" else "LLM_API_KEY"
                    _upsert_env(env_name, new_val)
                # 从 models.json 的数据里移除这两个字段（永远只存环境变量）
                data.pop(field, None)

        # 合并写入 models.json（保留 models/voice 等其他字段）
        current = _read_json("models.json")
        current.update(data)
        # 永远不要在 models.json 里存敏感 key
        for field in SENSITIVE_KEYS:
            current.pop(field, None)
        _write_json("models.json", current)
        return {"status": "ok"}
    except HTTPException:
        # 业务异常原样透传，别被下面的兜底重新包装成 500
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ========== 提示词配置 ==========

@router.get("/prompts")
async def get_prompts():
    return _read_json("prompts.json")


@router.put("/prompts")
async def update_prompts(data: dict):
    try:
        _write_json("prompts.json", data)
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
