"""
ContextPacker -- 上下文打包器（whole_codebase/selective 双模式）。

将项目文件打包成适合 Gemini 1M token 上下文窗口的 prompt 前缀。
支持两种策略:
  - whole_codebase: 扫描整个项目目录，将所有文本文件内联到上下文。
  - selective:    仅打包指定的文件列表，适合后期阶段只需部分文件时使用。

核心职责:
  1. 扫描项目目录，按扩展名过滤文本文件
  2. 生成带有文件路径标记的结构化 prompt 前缀
  3. 估算 token 数量，确保不超出 token_budget
  4. 支持 .gitignore 规则排除文件
  5. 支持缓存键生成（用于 Gemini Context Caching）
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Optional

__all__ = ["ContextPacker", "PackResult", "PACK_STRATEGIES"]


# ============================================================================
# 策略常量
# ============================================================================

PACK_STRATEGIES = ("whole_codebase", "selective")  # type: tuple[str, ...]

# 被视为文本文件的扩展名集合
_TEXT_EXTENSIONS: frozenset = frozenset({
    ".py", ".pyi", ".js", ".ts", ".jsx", ".tsx", ".rs", ".go",
    ".java", ".c", ".cpp", ".h", ".hpp", ".rb", ".php",
    ".swift", ".kt", ".scala", ".cs", ".r", ".jl", ".lua",
    ".sh", ".bash", ".zsh", ".ps1", ".bat",
    ".md", ".rst", ".txt", ".yaml", ".yml", ".json",
    ".toml", ".ini", ".cfg", ".xml", ".html", ".css", ".scss", ".less",
    ".sql", ".gitignore", ".env", ".editorconfig", ".dockerfile",
    ".proto", ".graphql", ".sol",
})

# 始终排除的目录
_ALWAYS_EXCLUDE_DIRS: frozenset = frozenset({
    ".git", "__pycache__", ".pytest_cache", ".tox", ".mypy_cache",
    ".ruff_cache", "node_modules", ".venv", "venv", "dist", "build",
    ".eggs", "*.egg-info", ".claude",
})

# 始终排除的文件模式
_ALWAYS_EXCLUDE_FILES: frozenset = frozenset({
    ".DS_Store", "Thumbs.db", ".coverage", "*.pyc", "*.pyo",
})

# 估算 token 比率（每字符约 0.25 个 token，英文代码）
_TOKENS_PER_CHAR = 0.25


# ============================================================================
# 数据类
# ============================================================================


@dataclass
class PackResult:
    """上下文打包结果。

    Attributes:
        prompt_prefix: 构建好的 prompt 前缀字符串。
        files_included: 成功打包的文件路径列表。
        files_excluded: 被排除的文件路径及原因。
        total_chars: 前缀总字符数。
        estimated_tokens: 估算的 token 数量。
        truncated: 是否因超出 token_budget 而发生截断。
    """
    prompt_prefix: str = ""
    files_included: list = field(default_factory=list)
    files_excluded: list = field(default_factory=list)
    total_chars: int = 0
    estimated_tokens: int = 0
    truncated: bool = False


# ============================================================================
# ContextPacker 主类
# ============================================================================


class ContextPacker:
    """上下文打包器 -- 将项目文件打包为 LLM prompt 前缀。

    支持两种打包策略:
      - whole_codebase: 扫描指定目录下所有文本文件并打包。
      - selective:     仅打包调用方指定的文件列表。

    始终遵守 .gitignore 排除规则和内置的排除目录/文件列表。

    Attributes:
        max_file_bytes: 单个文件最大读取字节数（默认 200KB）。
        token_budget: 上下文窗口 token 预算上限。
        extra_exclude_dirs: 用户额外排除的目录名集合。
        extra_exclude_patterns: 用户额外排除的文件名模式集合。
    """

    def __init__(
        self,
        *,
        max_file_bytes: int = 200 * 1024,
        token_budget: int = 900_000,
        extra_exclude_dirs: Optional[set] = None,
        extra_exclude_patterns: Optional[set] = None,
    ) -> None:
        """初始化上下文打包器。

        Args:
            max_file_bytes: 单个文件最大读取字节数，超出则截断。
            token_budget: 上下文窗口 token 预算上限。
            extra_exclude_dirs: 额外排除的目录名。
            extra_exclude_patterns: 额外排除的文件名模式。
        """
        self.max_file_bytes = max_file_bytes
        self.token_budget = token_budget
        self.extra_exclude_dirs = extra_exclude_dirs or set()
        self.extra_exclude_patterns = extra_exclude_patterns or set()

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def pack(
        self,
        root_dir: str = ".",
        *,
        strategy: str = "whole_codebase",
        file_list: Optional[list[str]] = None,
        system_prompt: str = "",
        state_summary: str = "",
    ) -> PackResult:
        """执行上下文打包。

        Args:
            root_dir: 项目根目录。
            strategy: 打包策略 -- "whole_codebase" 或 "selective"。
            file_list: selective 模式下指定的文件路径列表。
            system_prompt: 系统提示词，放在打包内容之前。
            state_summary: 状态摘要，放在系统提示词之后。

        Returns:
            PackResult 包含构建好的 prompt 前缀和元数据。

        Raises:
            ValueError: 策略无效或 selective 模式未提供 file_list。
        """
        if strategy not in PACK_STRATEGIES:
            raise ValueError(
                f"无效的打包策略 '{strategy}'，"
                f"必须是: {PACK_STRATEGIES}"
            )

        if strategy == "selective" and not file_list:
            raise ValueError("selective 策略必须提供 file_list 参数")

        # 获取文件列表
        if strategy == "whole_codebase":
            files = self._scan_directory(root_dir)
        else:
            files = list(file_list)

        # 打包文件内容
        prefix = self._build_prefix(root_dir, files)
        return prefix

    def pack_with_cache(
        self,
        root_dir: str = ".",
        *,
        strategy: str = "whole_codebase",
        file_list: Optional[list[str]] = None,
        system_prompt: str = "",
        state_summary: str = "",
    ) -> dict:
        """执行上下文打包并生成缓存键（用于 Gemini Context Caching）。

        将上下文分为静态部分（系统提示词、状态摘要、文件内容）
        和动态部分（阶段特定的指令）。为静态部分生成 SHA-256 缓存键。

        Args:
            root_dir: 项目根目录。
            strategy: 打包策略。
            file_list: selective 模式的文件列表。
            system_prompt: 系统提示词。
            state_summary: 状态摘要。

        Returns:
            包含 static_prefix、cache_key、dynamic_content、
            full_prompt 键的字典。
        """
        result = self.pack(
            root_dir,
            strategy=strategy,
            file_list=file_list,
            system_prompt=system_prompt,
            state_summary=state_summary,
        )

        static = result.prompt_prefix
        cache_key = self._compute_cache_key(static)

        return {
            "static_prefix": static,
            "cache_key": cache_key,
            "dynamic_content": "",
            "full_prompt": static,
            "estimated_tokens": result.estimated_tokens,
        }

    # ------------------------------------------------------------------
    # 文件扫描
    # ------------------------------------------------------------------

    def _scan_directory(self, root_dir: str) -> list[str]:
        """递归扫描目录，收集所有文本文件路径。

        排除 .gitignore 中列出的模式、内置排除目录和用户额外排除项。

        Args:
            root_dir: 扫描根目录。

        Returns:
            按相对路径排序的文件路径列表。
        """
        all_exclude_dirs = _ALWAYS_EXCLUDE_DIRS | self.extra_exclude_dirs
        files: list[str] = []

        # 读取 .gitignore 排除模式
        gitignore_patterns = self._parse_gitignore(root_dir)

        for dirpath, dirnames, filenames in os.walk(root_dir):
            # 原地修改 dirnames 以跳过排除的目录
            dirnames[:] = [
                d for d in dirnames
                if d not in all_exclude_dirs
                and not d.endswith(".egg-info")
            ]

            rel_dir = os.path.relpath(dirpath, root_dir)
            if rel_dir == ".":
                rel_dir = ""

            for fname in sorted(filenames):
                # 跳过非文本文件
                ext = os.path.splitext(fname)[1].lower()
                if ext not in _TEXT_EXTENSIONS:
                    continue
                # 跳过排除的文件
                if self._is_excluded(fname, gitignore_patterns):
                    continue

                rel_path = os.path.join(rel_dir, fname) if rel_dir else fname
                abs_path = os.path.join(dirpath, fname)
                files.append(abs_path)

        return sorted(files)

    # ------------------------------------------------------------------
    # 前缀构建
    # ------------------------------------------------------------------

    def _build_prefix(
        self, root_dir: str, files: list[str]
    ) -> PackResult:
        """将文件列表构建为结构化的 prompt 前缀。

        格式:
            === 文件路径 ===
            文件内容...
            === END ===

        超出 token_budget 时触发截断。

        Args:
            root_dir: 项目根目录（用于计算相对路径）。
            files: 绝对文件路径列表。

        Returns:
            PackResult 实例。
        """
        result = PackResult()
        parts: list[str] = []
        total_chars = 0
        max_chars = int(self.token_budget / max(_TOKENS_PER_CHAR, 0.01))

        for abs_path in files:
            try:
                rel_path = os.path.relpath(abs_path, root_dir)
            except ValueError:
                rel_path = abs_path

            # 检查文件大小
            try:
                fsize = os.path.getsize(abs_path)
            except OSError:
                result.files_excluded.append((rel_path, "无法获取文件大小"))
                continue

            if fsize == 0:
                result.files_excluded.append((rel_path, "空文件"))
                continue

            # 读取文件内容
            try:
                with open(abs_path, "r", encoding="utf-8",
                          errors="replace") as f:
                    if fsize <= self.max_file_bytes:
                        content = f.read()
                    else:
                        content = f.read(self.max_file_bytes)
                        content += (
                            f"\n... [截断: "
                            f"{fsize - self.max_file_bytes} 字节已省略]"
                        )
            except (OSError, UnicodeDecodeError) as e:
                result.files_excluded.append(
                    (rel_path, f"读取错误: {e}")
                )
                continue

            # 构建文件块
            block = f"=== {rel_path} ===\n{content}\n=== END ===\n"

            # 检查 token 预算
            if total_chars + len(block) > max_chars:
                result.truncated = True
                break

            parts.append(block)
            total_chars += len(block)
            result.files_included.append(rel_path)

        result.prompt_prefix = "\n".join(parts)
        result.total_chars = total_chars
        result.estimated_tokens = int(total_chars * _TOKENS_PER_CHAR)
        return result

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _is_excluded(
        self, fname: str, gitignore_patterns: list[str]
    ) -> bool:
        """检查文件名是否应被排除。

        Args:
            fname: 文件名。
            gitignore_patterns: .gitignore 中的模式列表。

        Returns:
            True 表示应排除。
        """
        # 内置排除
        if fname in _ALWAYS_EXCLUDE_FILES:
            return True
        # 用户额外排除
        if fname in self.extra_exclude_patterns:
            return True
        # .gitignore 模式匹配
        for pattern in gitignore_patterns:
            if self._match_gitignore(pattern, fname):
                return True
        return False

    @staticmethod
    def _parse_gitignore(root_dir: str) -> list[str]:
        """解析 .gitignore 文件，提取排除模式。

        Args:
            root_dir: 项目根目录。

        Returns:
            非空、非注释的排除模式列表。
        """
        gi_path = os.path.join(root_dir, ".gitignore")
        patterns: list[str] = []
        if not os.path.isfile(gi_path):
            return patterns
        try:
            with open(gi_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        patterns.append(stripped)
        except OSError:
            pass
        return patterns

    @staticmethod
    def _match_gitignore(pattern: str, fname: str) -> bool:
        """简单的 .gitignore 模式匹配。

        支持通配符 * 和目录模式（以 / 结尾）。

        Args:
            pattern: gitignore 模式。
            fname: 要匹配的文件名。

        Returns:
            True 表示匹配。
        """
        import fnmatch
        # 目录模式
        if pattern.endswith("/"):
            return False  # 文件不会是目录
        # 去除前导 /
        if pattern.startswith("/"):
            pattern = pattern[1:]
        return fnmatch.fnmatch(fname, pattern)

    @staticmethod
    def _compute_cache_key(content: str) -> str:
        """为上下文内容生成 SHA-256 缓存键。

        Args:
            content: 要生成缓存键的静态内容字符串。

        Returns:
            32 字符的十六进制哈希字符串。
        """
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:32]
