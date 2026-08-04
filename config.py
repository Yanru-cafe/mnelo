"""
mnelo config — load settings from environment variables or config file.

[ 7/18 P2, 7/19 v0.5.0]
来源优先级 (高到低):
1. 环境变量 MNELO_MEMORY_* (部署, systemd/launchd)
2. 配置文件 LIVE_ROOT/config.toml (本地覆盖, 默认 ~/.hermes/memory/config.toml)
3. 默认值 (localtime)

[配置项]
- timezone: 'local' / 'utc' / 'Asia/Shanghai' (任意 IANA tz)
  默认 'local' (用系统本地时区)
- warm_up_embedder: bool
  默认 True (Memory 启动时加载 embedding 模型, 避免首次 recall 1s 冷启动)
- embedder_model: str
  默认 'BAAI/bge-small-zh-v1.5' (中文原生, 512d)
  切换: 'BAAI/bge-small-en-v1.5' (英文, 384d)
       | 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2' (50+ 语种, 384d, 含日/韩/西/法)
- embedder_dim: int
  默认 512. 必须与模型实际输出维度一致 (mnelo 用它建 sqlite-vec 表 schema)
- server.host: str
  默认 '127.0.0.1' (loopback-only, P2-1 安全防线)
- server.port: int
  默认 8086 (与 launchd plist 默认一致, 可改到 1024-65535)
"""

import os
import sys
from pathlib import Path
from typing import Optional

try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # 3.10 and below
    except ImportError:
        tomllib = None


# [7/21 fix] LIVE_ROOT 单一事实源 — 消除硬编码的 /Users/apple/.hermes/memory。
# 解析优先级: env MNELO_MEMORY_DIR > ~/.hermes/memory (默认, 与旧部署向后兼容)。
# 所有需要 live 目录/DB 路径的模块 (memory / entity_resolve / scripts) 都从这里读。
DEFAULT_LIVE_ROOT = Path(os.environ.get("MNELO_MEMORY_DIR", str(Path.home() / ".hermes" / "memory")))
CONFIG_PATH = Path(os.environ.get("MNELO_MEMORY_CONFIG", str(DEFAULT_LIVE_ROOT / "config.toml")))


def resolve_db_path() -> Path:
    """Resolve the memory.db path: env MNELO_MEMORY_DB_PATH > MNELO_MEMORY_DIR > ~/.hermes/memory.

    Used by memory.py / entity_resolve.py / scripts to avoid hardcoding an absolute path.
    """
    env_db = os.environ.get("MNELO_MEMORY_DB_PATH")
    if env_db:
        return Path(env_db)
    env_dir = os.environ.get("MNELO_MEMORY_DIR")
    if env_dir:
        return Path(env_dir) / "memory.db"
    return DEFAULT_LIVE_ROOT / "memory.db"


def _load_config_file(path: Path) -> dict:
    """Load TOML config file. Returns empty dict if not found or parse fails."""
    if not path.exists() or tomllib is None:
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        print(f"[config] WARN: failed to load {path}: {e}", file=sys.stderr)
        return {}


def _resolve_tz(value: Optional[str]) -> str:
    """Resolve timezone setting.

    Args:
        value: 'local' / 'utc' / '<IANA tz>' / None

    Returns:
        - 'local' → use system local time (datetime.now(tz=None))
        - 'utc' → use UTC
        - '<IANA tz>' → use that tz (e.g. 'Asia/Shanghai')

    Raises:
        ValueError: invalid value
    """
    if value is None:
        return "local"  # 默认
    v = value.strip().lower()
    if v in ("local", "utc"):
        return v
    # IANA tz name (e.g. Asia/Shanghai). 不强制 import pytz, 让 datetime 自己解析
    return value


class Config:
    """Loaded config singleton."""

    _instance: Optional["Config"] = None

    def __init__(self):
        self._raw = _load_config_file(CONFIG_PATH)

        # Timezone: env > file > default (local)
        self.timezone = _resolve_tz(os.environ.get("MNELO_MEMORY_TIMEZONE") or self._raw.get("timezone"))

        # Warm-up: env > file > default (True)
        warm_str = os.environ.get("MNELO_MEMORY_WARM_UP_EMBEDDER") or str(self._raw.get("warm_up_embedder", True))
        self.warm_up_embedder = warm_str.lower() not in ("false", "0", "no", "off")

        # Embedder model: env > file > default (bge-small-zh-v1.5, 512d)
        # 允许 env override (e.g. MNELO_MEMORY_EMBEDDER_MODEL=BAAI/bge-small-en-v1.5)
        # TOML key: embedder.model (嵌套 section)
        embedder_section = self._raw.get("embedder", {}) if isinstance(self._raw.get("embedder"), dict) else {}
        self.embedder_model = (
            os.environ.get("MNELO_MEMORY_EMBEDDER_MODEL")
            or embedder_section.get("model")
            or self._raw.get("embedder_model")  # 兼容旧扁平 key
            or "BAAI/bge-small-zh-v1.5"
        )

        # Embedder dim: env > file > default (512)
        # 必须与 model 实际输出维度一致 — 错配会让 sqlite-vec insert 失败
        dim_str = (
            os.environ.get("MNELO_MEMORY_EMBEDDER_DIM")
            or str(embedder_section.get("dim", ""))
            or str(self._raw.get("embedder_dim", ""))
            or "512"
        )
        try:
            self.embedder_dim = int(dim_str)
        except ValueError:
            print(f'[config] WARN: embedder_dim "{dim_str}" 不是整数, 回落 512', file=sys.stderr)
            self.embedder_dim = 512

        # [Round 2 quality audit] server.host + server.port 配置
        server_section = self._raw.get("server", {}) if isinstance(self._raw.get("server"), dict) else {}
        self.server_host = os.environ.get("MNELO_MEMORY_SERVER_HOST") or server_section.get("host") or "127.0.0.1"
        port_str = os.environ.get("MNELO_MEMORY_SERVER_PORT") or str(server_section.get("port", "")) or ""
        try:
            port = int(port_str) if port_str else 8086
            if not (1024 <= port <= 65535):
                raise ValueError(f"port {port} out of range")
        except ValueError as e:
            print(f'[config] WARN: server.port "{port_str}" invalid ({e}); 回落 8086', file=sys.stderr)
            port = 8086
        self.server_port = port

        # [7/21 fix] Storage location: env MNELO_MEMORY_DIR/MNELO_MEMORY_DB_PATH
        # > config.toml [storage].dir > ~/.hermes/memory (backward compatible).
        storage_section = self._raw.get("storage", {}) if isinstance(self._raw.get("storage"), dict) else {}
        env_dir = os.environ.get("MNELO_MEMORY_DIR")
        env_db = os.environ.get("MNELO_MEMORY_DB_PATH")
        self.db_dir = Path(env_dir or storage_section.get("dir") or DEFAULT_LIVE_ROOT)
        self.db_path = Path(env_db or (self.db_dir / "memory.db"))

        # [zvec 集成] SearchIndex 后端 (DESIGN §3.6/§8.3):
        # env MNELO_MEMORY_SEARCH_BACKEND > config.toml [search].backend > 'sqlite_vec'.
        search_section = self._raw.get("search", {}) if isinstance(self._raw.get("search"), dict) else {}
        self.search_backend = (
            os.environ.get("MNELO_MEMORY_SEARCH_BACKEND")
            or search_section.get("backend")
            or "sqlite_vec"
        )

    @classmethod
    def load(cls) -> "Config":
        """Get the loaded config singleton."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def config_path(self) -> Path:
        """Where the config file is being loaded from."""
        return CONFIG_PATH

    def describe(self) -> str:
        """One-line summary for startup banner."""
        return f"tz={self.timezone} warm_up={self.warm_up_embedder} embedder={self.embedder_model}/{self.embedder_dim}d"


# Eager load on import
config = Config.load()
