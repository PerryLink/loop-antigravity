"""ContextPacker unit tests."""
import os

import pytest

from loop_antigravity.context_packer import (
    ContextPacker,
    PackResult,
    PACK_STRATEGIES,
)


# ---- Helpers ----

def _make_file(root, rel_path, content):
    """Create a file inside root and return its absolute path."""
    abs_path = os.path.join(root, rel_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(content)
    return abs_path


# ============================================================
# TestInit
# ============================================================

class TestContextPackerInit:
    def test_default_init(self):
        cp = ContextPacker()
        assert cp.max_file_bytes == 200 * 1024
        assert cp.token_budget == 900_000
        assert cp.extra_exclude_dirs == set()
        assert cp.extra_exclude_patterns == set()

    def test_custom_params(self):
        cp = ContextPacker(
            max_file_bytes=50000,
            token_budget=500_000,
            extra_exclude_dirs={"custom_dir"},
            extra_exclude_patterns={"*.secret"},
        )
        assert cp.max_file_bytes == 50000
        assert cp.token_budget == 500_000
        assert cp.extra_exclude_dirs == {"custom_dir"}
        assert cp.extra_exclude_patterns == {"*.secret"}

    def test_none_excludes_use_empty_set(self):
        cp = ContextPacker(extra_exclude_dirs=None, extra_exclude_patterns=None)
        assert cp.extra_exclude_dirs == set()
        assert cp.extra_exclude_patterns == set()


# ============================================================
# TestPackResult
# ============================================================

class TestPackResult:
    def test_default_values(self):
        pr = PackResult()
        assert pr.prompt_prefix == ""
        assert pr.files_included == []
        assert pr.files_excluded == []
        assert pr.total_chars == 0
        assert pr.estimated_tokens == 0
        assert pr.truncated is False

    def test_fields_settable(self):
        pr = PackResult(
            prompt_prefix="content",
            files_included=["a.py"],
            files_excluded=[("b.py", "too large")],
            total_chars=100,
            estimated_tokens=25,
            truncated=True,
        )
        assert pr.prompt_prefix == "content"
        assert pr.files_included == ["a.py"]
        assert len(pr.files_excluded) == 1
        assert pr.total_chars == 100
        assert pr.estimated_tokens == 25
        assert pr.truncated is True


# ============================================================
# TestPackWholeCodebase
# ============================================================

class TestPackWholeCodebase:
    def test_pack_single_file(self, tmp_path):
        root = str(tmp_path)
        _make_file(root, "test.py", "print('hello')")
        cp = ContextPacker()
        result = cp.pack(root, strategy="whole_codebase")
        assert isinstance(result, PackResult)
        assert len(result.files_included) == 1
        assert "test.py" in str(result.files_included)
        assert result.total_chars > 0
        assert result.estimated_tokens > 0

    def test_pack_multiple_files(self, tmp_path):
        root = str(tmp_path)
        _make_file(root, "a.py", "x = 1")
        _make_file(root, "b.py", "y = 2")
        cp = ContextPacker()
        result = cp.pack(root, strategy="whole_codebase")
        assert len(result.files_included) == 2

    def test_pack_excludes_non_text(self, tmp_path):
        root = str(tmp_path)
        _make_file(root, "code.py", "x = 1")
        _make_file(root, "image.png", "not text")
        cp = ContextPacker()
        result = cp.pack(root, strategy="whole_codebase")
        included_names = [os.path.basename(f) for f in result.files_included]
        assert "code.py" in included_names
        assert "image.png" not in included_names

    def test_pack_excludes_empty_file(self, tmp_path):
        root = str(tmp_path)
        _make_file(root, "code.py", "x = 1")
        _make_file(root, "empty.py", "")
        cp = ContextPacker()
        result = cp.pack(root, strategy="whole_codebase")
        included_names = [os.path.basename(f) for f in result.files_included]
        assert "code.py" in included_names
        assert "empty.py" not in included_names

    def test_pack_respects_extra_exclude_dirs(self, tmp_path):
        root = str(tmp_path)
        os.makedirs(os.path.join(root, "my_secrets"))
        _make_file(root, "src.py", "x = 1")
        _make_file(root, os.path.join("my_secrets", "key.py"), "SECRET")
        cp = ContextPacker(extra_exclude_dirs={"my_secrets"})
        result = cp.pack(root, strategy="whole_codebase")
        included_names = [os.path.basename(f) for f in result.files_included]
        assert "src.py" in included_names
        assert "key.py" not in included_names

    def test_pack_token_budget_truncation(self, tmp_path):
        root = str(tmp_path)
        for i in range(10):
            _make_file(root, f"file{i}.py", "x = 1\n" * 500)
        cp = ContextPacker(token_budget=100)
        result = cp.pack(root, strategy="whole_codebase")
        assert result.truncated is True

    def test_pack_large_file_truncated(self, tmp_path):
        root = str(tmp_path)
        content = "x\n" * 1000
        _make_file(root, "big.py", content)
        cp = ContextPacker(max_file_bytes=50, token_budget=2000)
        result = cp.pack(root, strategy="whole_codebase")
        assert result.files_included or result.files_excluded


# ============================================================
# TestPackSelective
# ============================================================

class TestPackSelective:
    def test_selective_requires_file_list(self):
        cp = ContextPacker()
        with pytest.raises(ValueError, match="file_list"):
            cp.pack(".", strategy="selective")

    def test_selective_packs_specified_files(self, tmp_path):
        root = str(tmp_path)
        f1 = _make_file(root, "a.py", "x = 1")
        f2 = _make_file(root, "b.py", "y = 2")
        _make_file(root, "c.py", "z = 3")
        cp = ContextPacker()
        result = cp.pack(
            root, strategy="selective", file_list=[f1, f2]
        )
        assert len(result.files_included) == 2

    def test_selective_includes_only_requested(self, tmp_path):
        root = str(tmp_path)
        f1 = _make_file(root, "a.py", "x = 1")
        _make_file(root, "b.py", "y = 2")
        cp = ContextPacker()
        result = cp.pack(
            root, strategy="selective", file_list=[f1]
        )
        assert len(result.files_included) == 1

    def test_selective_invalid_file_handled(self, tmp_path):
        root = str(tmp_path)
        f1 = _make_file(root, "a.py", "x = 1")
        fake = os.path.join(root, "nonexistent.py")
        cp = ContextPacker()
        result = cp.pack(
            root, strategy="selective", file_list=[f1, fake]
        )
        assert result is not None

    def test_selective_empty_list_raises(self):
        cp = ContextPacker()
        with pytest.raises(ValueError, match="file_list"):
            cp.pack(".", strategy="selective", file_list=[])


# ============================================================
# TestPackStrategiesError
# ============================================================

class TestPackStrategies:
    def test_invalid_strategy_raises(self):
        cp = ContextPacker()
        with pytest.raises(ValueError, match="无效的打包策略"):
            cp.pack(".", strategy="invalid_strategy")

    def test_valid_strategies_constant(self):
        assert "whole_codebase" in PACK_STRATEGIES
        assert "selective" in PACK_STRATEGIES
        assert len(PACK_STRATEGIES) == 2

    def test_both_strategies_accepted(self, tmp_path):
        root = str(tmp_path)
        f = _make_file(root, "a.py", "x = 1")
        cp = ContextPacker()
        r1 = cp.pack(root, strategy="whole_codebase")
        r2 = cp.pack(root, strategy="selective", file_list=[f])
        assert isinstance(r1, PackResult)
        assert isinstance(r2, PackResult)


# ============================================================
# TestPackWithCache
# ============================================================

class TestPackWithCache:
    def test_returns_dict(self, tmp_path):
        root = str(tmp_path)
        _make_file(root, "a.py", "x = 1")
        cp = ContextPacker()
        result = cp.pack_with_cache(root, strategy="whole_codebase")
        assert isinstance(result, dict)
        assert "static_prefix" in result
        assert "cache_key" in result
        assert "dynamic_content" in result
        assert "full_prompt" in result
        assert "estimated_tokens" in result

    def test_cache_key_is_string(self, tmp_path):
        root = str(tmp_path)
        _make_file(root, "a.py", "x = 1")
        cp = ContextPacker()
        result = cp.pack_with_cache(root, strategy="whole_codebase")
        assert isinstance(result["cache_key"], str)
        assert len(result["cache_key"]) == 32

    def test_same_content_same_cache_key(self, tmp_path):
        root = str(tmp_path)
        _make_file(root, "a.py", "print(1)")
        cp = ContextPacker()
        r1 = cp.pack_with_cache(root, strategy="whole_codebase")
        r2 = cp.pack_with_cache(root, strategy="whole_codebase")
        assert r1["cache_key"] == r2["cache_key"]

    def test_different_content_different_cache_key(self, tmp_path):
        root1 = str(tmp_path / "proj1")
        root2 = str(tmp_path / "proj2")
        _make_file(root1, "a.py", "x = 1")
        _make_file(root2, "a.py", "y = 2")
        cp = ContextPacker()
        r1 = cp.pack_with_cache(root1, strategy="whole_codebase")
        r2 = cp.pack_with_cache(root2, strategy="whole_codebase")
        assert r1["cache_key"] != r2["cache_key"]

    def test_empty_scenario(self, tmp_path):
        root = str(tmp_path)
        cp = ContextPacker()
        result = cp.pack_with_cache(root, strategy="whole_codebase")
        assert result["cache_key"] == ContextPacker._compute_cache_key("")


# ============================================================
# TestSystemPromptAndState
# ============================================================

class TestSystemPromptAndState:
    def test_pack_accepts_system_prompt(self, tmp_path):
        root = str(tmp_path)
        _make_file(root, "a.py", "x = 1")
        cp = ContextPacker()
        result = cp.pack(
            root,
            strategy="whole_codebase",
            system_prompt="You are a helpful assistant.",
            state_summary="Current state: ready.",
        )
        assert isinstance(result, PackResult)

    def test_pack_with_cache_accepts_prompts(self, tmp_path):
        root = str(tmp_path)
        _make_file(root, "a.py", "x = 1")
        cp = ContextPacker()
        result = cp.pack_with_cache(
            root,
            strategy="whole_codebase",
            system_prompt="SYSTEM",
            state_summary="STATE",
        )
        assert isinstance(result, dict)
        assert result["cache_key"]


# ============================================================
# TestParseGitignore
# ============================================================

class TestParseGitignore:
    def test_gitignore_exists_with_patterns(self, tmp_path):
        root = str(tmp_path)
        gi_path = os.path.join(root, ".gitignore")
        with open(gi_path, "w", encoding="utf-8") as f:
            f.write("*.pyc\n")
            f.write("# comment line\n")
            f.write(".env\n")
            f.write("\n")
        patterns = ContextPacker._parse_gitignore(root)
        assert "*.pyc" in patterns
        assert ".env" in patterns
        assert "# comment line" not in patterns
        assert "" not in patterns

    def test_gitignore_not_found(self, tmp_path):
        root = str(tmp_path)
        patterns = ContextPacker._parse_gitignore(root)
        assert patterns == []

    def test_gitignore_unreadable(self, tmp_path):
        root = str(tmp_path)
        gi_path = os.path.join(root, ".gitignore")
        # Create a directory with same name to cause OSError
        os.makedirs(gi_path, exist_ok=True)
        patterns = ContextPacker._parse_gitignore(root)
        assert patterns == []


# ============================================================
# TestMatchGitignore
# ============================================================

class TestMatchGitignore:
    def test_simple_star_match(self):
        assert ContextPacker._match_gitignore("*.pyc", "test.pyc") is True

    def test_simple_star_no_match(self):
        assert ContextPacker._match_gitignore("*.pyc", "test.py") is False

    def test_directory_pattern_no_match(self):
        # 目录模式 (ends with /)：文件不会是目录
        assert ContextPacker._match_gitignore("build/", "build") is False

    def test_leading_slash_pattern(self):
        # 去除前导 / 后应匹配
        assert ContextPacker._match_gitignore("/.env", ".env") is True

    def test_leading_slash_no_match(self):
        assert ContextPacker._match_gitignore("/config.py", "src/config.py") is False

    def test_wildcard_in_middle(self):
        assert ContextPacker._match_gitignore("*.log", "error.log") is True


# ============================================================
# TestIsExcluded
# ============================================================

class TestIsExcluded:
    def test_always_exclude_files(self):
        cp = ContextPacker()
        assert cp._is_excluded("Thumbs.db", []) is True
        assert cp._is_excluded(".DS_Store", []) is True

    def test_extra_exclude_patterns(self):
        cp = ContextPacker(extra_exclude_patterns={"secret.txt"})
        assert cp._is_excluded("secret.txt", []) is True

    def test_extra_exclude_not_matching(self):
        cp = ContextPacker(extra_exclude_patterns={"secret.txt"})
        assert cp._is_excluded("normal.txt", []) is False

    def test_gitignore_pattern_matches(self):
        cp = ContextPacker()
        assert cp._is_excluded("test.pyc", ["*.pyc"]) is True

    def test_gitignore_pattern_no_match(self):
        cp = ContextPacker()
        assert cp._is_excluded("test.py", ["*.pyc"]) is False

    def test_normal_file_not_excluded(self):
        cp = ContextPacker()
        assert cp._is_excluded("main.py", []) is False


# ============================================================
# TestBuildPrefixEdgeCases
# ============================================================

class TestBuildPrefixEdgeCases:
    def test_relpath_value_error(self, tmp_path):
        """os.path.relpath 在不同驱动器间抛出 ValueError 时应回退。"""
        import platform
        if platform.system() != "Windows":
            pytest.skip("ValueError from os.path.relpath different drives is Windows-specific")
        # 用一个在另一驱动器上的路径模拟
        root = str(tmp_path)
        f = _make_file(root, "a.py", "x = 1")
        cp = ContextPacker()
        # 使用绝对路径、valid 文件来调用 _build_prefix
        result = cp._build_prefix(root, [f])
        assert result is not None

    def test_file_read_os_error(self, tmp_path, monkeypatch):
        """文件读取失败 (OSError) 应被正确处理。"""
        from unittest import mock
        root = str(tmp_path)
        f = _make_file(root, "a.py", "x = 1")
        cp = ContextPacker()

        # Mock open to raise OSError for the test file
        original_open = open

        def failing_open(path, *args, **kwargs):
            if isinstance(path, str) and "a.py" in path and "rb" not in str(args):
                raise OSError("simulated read error")
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr("builtins.open", failing_open)
        result = cp._build_prefix(root, [f])
        assert len(result.files_included) == 0
        assert len(result.files_excluded) > 0

    def test_oserror_on_getsize(self, tmp_path):
        """os.path.getsize 失败时应排除该文件。"""
        root = str(tmp_path)
        nonexistent = os.path.join(root, "nonexistent.py")
        cp = ContextPacker()
        result = cp._build_prefix(root, [nonexistent])
        # 文件不存在，应被排除
        assert len(result.files_included) == 0
        assert len(result.files_excluded) > 0

    def test_unicode_decode_error_handled(self, tmp_path, monkeypatch):
        """二进制文件读取时 errors='replace' 会替换非法字符，但仍成功读取。
        要触发 UnicodeDecodeError 需要关闭 errors='replace'。"""
        from unittest import mock
        root = str(tmp_path)
        fpath = os.path.join(root, "bad.py")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write("valid")

        cp = ContextPacker()

        # Mock open to simulate a file that raises UnicodeDecodeError
        original_open = open

        def failing_open(file, mode="r", *args, **kwargs):
            if isinstance(file, str) and "bad.py" in file and "r" in str(mode):
                raise UnicodeDecodeError("utf-8", b"\x80", 0, 1, "invalid")
            return original_open(file, mode, *args, **kwargs)

        monkeypatch.setattr("builtins.open", failing_open)
        result = cp._build_prefix(root, [fpath])
        assert len(result.files_included) == 0
        assert len(result.files_excluded) > 0

    def test_empty_file_excluded(self, tmp_path):
        """空文件应在构建前缀时被排除。"""
        root = str(tmp_path)
        fpath = os.path.join(root, "empty.py")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write("")
        cp = ContextPacker()
        result = cp._build_prefix(root, [fpath])
        assert "empty.py" not in str(result.files_included)
        assert len(result.files_excluded) > 0


# ============================================================
# TestPackWithGitignore
# ============================================================

class TestPackWithGitignore:
    def test_pack_respects_gitignore(self, tmp_path):
        """pack 应遵守 .gitignore 中的排除规则。
        使用扩展名在 _TEXT_EXTENSIONS 中的文件来确保覆盖 gitignore 排除路径。"""
        root = str(tmp_path)
        # 创建 .gitignore
        gi_path = os.path.join(root, ".gitignore")
        with open(gi_path, "w", encoding="utf-8") as f:
            f.write("secret.py\n")
        _make_file(root, "main.py", "x = 1")
        _make_file(root, "secret.py", "SECRET = 'password'")
        cp = ContextPacker()
        result = cp.pack(root, strategy="whole_codebase")
        included_names = [os.path.basename(f) for f in result.files_included]
        assert "main.py" in included_names
        assert "secret.py" not in included_names


# ============================================================
# TestParseGitignoreOSError
# ============================================================

class TestParseGitignoreOSError:
    def test_gitignore_read_oserror(self, tmp_path, monkeypatch):
        """.gitignore 存在但不可读时应捕获 OSError。"""
        from unittest import mock
        root = str(tmp_path)
        gi_path = os.path.join(root, ".gitignore")
        # 创建正常的 .gitignore 文件
        with open(gi_path, "w", encoding="utf-8") as f:
            f.write("*.pyc\n")

        original_open = open

        def failing_open(path, mode="r", *args, **kwargs):
            if isinstance(path, str) and path.endswith(".gitignore") and "r" in str(mode):
                raise OSError("permission denied")
            return original_open(path, mode, *args, **kwargs)

        monkeypatch.setattr("builtins.open", failing_open)
        patterns = ContextPacker._parse_gitignore(root)
        assert patterns == []


# ============================================================
# TestRelpathEdgeCase
# ============================================================

class TestRelpathEdgeCase:
    def test_relpath_value_error_fallback(self, monkeypatch):
        """os.path.relpath 抛出 ValueError 时应回退到绝对路径。"""
        from unittest import mock

        # Mock os.path.relpath to raise ValueError
        def relpath_side_effect(path, start):
            raise ValueError("path is on a different drive")

        monkeypatch.setattr("os.path.relpath", relpath_side_effect)

        cp = ContextPacker()
        # _build_prefix with a mock root that triggers relpath ValueError
        result = cp._build_prefix("/fake_root", ["/other_drive/file.py"])
        assert isinstance(result, PackResult)
