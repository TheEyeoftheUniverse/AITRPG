import json
import os
import re
import time
from typing import Any, Dict, Optional

from astrbot.api import logger


class JsonSaveStore:
    """基于文件系统的简单 JSON 存档存储"""

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def load(self, key: str) -> Optional[Dict[str, Any]]:
        """读取指定 key 的存档"""
        path = self._get_save_path(key)
        if not os.path.exists(path):
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logger.warning(f"[AITRPG] 读取存档失败，JSON格式错误: {path}, error={e}")
            return None
        except OSError as e:
            logger.warning(f"[AITRPG] 读取存档失败: {path}, error={e}")
            return None

    def save(self, key: str, data: Dict[str, Any]):
        """写入指定 key 的存档"""
        path = self._get_save_path(key)
        temp_path = f"{path}.tmp"

        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, path)
        except OSError as e:
            logger.warning(f"[AITRPG] 写入存档失败: {path}, error={e}")
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass

    def delete(self, key: str):
        """删除指定 key 的存档"""
        path = self._get_save_path(key)
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as e:
            logger.warning(f"[AITRPG] 删除存档失败: {path}, error={e}")

    def _get_save_path(self, key: str) -> str:
        safe_key = re.sub(r"[^a-zA-Z0-9_.-]", "_", key)
        return os.path.join(self.base_dir, f"{safe_key}.json")

    def cleanup_stale(self, max_age_seconds: int = 86400 * 7, active_keys: set = None):
        """删除超过 max_age_seconds 未修改的存档文件，跳过 active_keys 中的。"""
        active_keys = active_keys or set()
        now = time.time()
        removed = 0
        try:
            for fname in os.listdir(self.base_dir):
                if not fname.endswith(".json"):
                    continue
                key = fname[:-5]
                if key in active_keys:
                    continue
                fpath = os.path.join(self.base_dir, fname)
                try:
                    mtime = os.path.getmtime(fpath)
                    if now - mtime > max_age_seconds:
                        os.remove(fpath)
                        removed += 1
                except OSError:
                    pass
        except OSError:
            pass
        if removed:
            logger.info(f"[AITRPG] 清理了 {removed} 个过期存档（>{max_age_seconds // 86400}天）")
