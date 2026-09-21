"""
key_manager.py —— 统一管理敏感 API key 的加载。

优先级：
  1. 环境变量（DASHSCOPE_API_KEY / LLM_API_KEY）
  2. backend/.env 文件（同格式，本地开发用）
  3. config/models.json 里的 api_key / llm_api_key（向后兼容）

设计：
  - 两个 key 独立：DashScope 视频/TTS 用 DASHSCOPE_API_KEY，LLM 用 LLM_API_KEY
  - 如果只设了一个，另一个默认复用（向后兼容老配置）
  - 不在任何返回里暴露完整 key
"""
import os
from pathlib import Path


def _load_dotenv(dotenv_path: Path):
    """极简 .env 解析：KEY=VALUE 形式，忽略注释和空行"""
    if not dotenv_path.exists():
        return
    with open(dotenv_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                continue
            key, _, value = line.partition('=')
            key = key.strip()
            value = value.strip()
            # 去掉两端引号
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            # 不覆盖已存在的环境变量（进程环境优先）
            if key and key not in os.environ:
                os.environ[key] = value


def _load_config_keys() -> tuple:
    """从 models.json 加载兜底 key（向后兼容）"""
    try:
        from services.optimizer import _load_config
        cfg = _load_config()
        return cfg.get('api_key', ''), cfg.get('llm_api_key', '')
    except Exception:
        return '', ''


def _init():
    dotenv_path = Path(__file__).parent.parent / '.env'
    _load_dotenv(dotenv_path)


# 模块加载时自动解析 .env
_init()


def get_dashscope_api_key() -> str:
    """DashScope 视频 / TTS 用的 key"""
    key = os.environ.get('DASHSCOPE_API_KEY', '').strip()
    if key:
        return key
    # fallback：环境变量没有 → 复用 LLM_API_KEY → 再 fallback 到 models.json
    key = os.environ.get('LLM_API_KEY', '').strip()
    if key:
        return key
    cfg_api, _ = _load_config_keys()
    return cfg_api


def get_llm_api_key() -> str:
    """LLM（qwen 等）用的 key"""
    key = os.environ.get('LLM_API_KEY', '').strip()
    if key:
        return key
    # fallback：复用 DASHSCOPE_API_KEY → 再 fallback 到 models.json
    key = os.environ.get('DASHSCOPE_API_KEY', '').strip()
    if key:
        return key
    _, cfg_llm = _load_config_keys()
    return cfg_llm or get_dashscope_api_key()


def mask_key(key: str) -> str:
    """把 key 显示为 'sk-ws-****...****DH' 形式，只露前 6 + 后 4 字符"""
    if not key:
        return ''
    if len(key) <= 12:
        return key[:2] + '***' + key[-2:]
    return key[:6] + '***' + key[-4:]
