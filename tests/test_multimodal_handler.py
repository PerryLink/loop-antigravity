"""MultimodalHandler unit tests."""
import pytest
from loop_antigravity.multimodal_handler import MultimodalHandler as H

# ---- Test binary payloads ----
P = b'\x89PNG\r\n\x1a\n' + b'\x00' * 50
J = b'\xff\xd8\xff\xe0' + b'\x00' * 50
F = b'%PDF-1.4\n' + b'\x00' * 50
G = b'GIF89a' + b'\x00' * 50
Wb = b'RIFF\x00\x00\x00\x00WEBP' + b'\x00' * 50
Wav = b'RIFF\x00\x00\x00\x00WAVE' + b'\x00' * 50
M = b'\x00\x00\x00\x18ftyp' + b'\x00' * 50
B = b'\x00' * 100
ID3 = b'ID3' + b'\x00' * 50


def _f(tmp_path, name, content):
    """Create a temp file and return its Path."""
    p = tmp_path / name
    p.write_bytes(content)
    return p


# ============================================================
# TestInit
# ============================================================

class TestInit:
    def test_default(self):
        h = H()
        assert h.size_limit > 0 and h.pdf_enabled

    def test_custom_size(self):
        h = H(size_limit=1048576)
        assert h.size_limit == 1048576

    def test_pdf_disabled(self):
        h = H(pdf_enabled=False)
        assert not h.pdf_enabled

    def test_negative_size_raises(self):
        pytest.raises(ValueError, H, size_limit=-1)

    def test_zero_size_limit(self):
        h = H(size_limit=0)
        assert h.size_limit == 0

    def test_large_size_limit(self):
        h = H(size_limit=1073741824)  # 1 GB
        assert h.size_limit == 1073741824

    def test_properties_are_readonly(self):
        h = H()
        with pytest.raises(AttributeError):
            h.size_limit = 100


# ============================================================
# TestDetectType
# ============================================================

class TestDetectType:
    @pytest.mark.parametrize("n, c, e", [
        ("a", P, "PNG"),
        ("b", J, "JPEG"),
        ("c", F, "PDF"),
        ("d", G, "GIF"),
        ("e", Wb, "WEBP"),
        ("f", B, "UNKNOWN"),
    ])
    def test_magic_bytes(self, tmp_path, n, c, e):
        assert H().detect_type(_f(tmp_path, n, c)) == e

    @pytest.mark.parametrize("ext, expected", [
        (".jpg", "JPEG"),
        (".jpeg", "JPEG"),
        (".gif", "GIF"),
        (".webp", "WEBP"),
        (".bmp", "BMP"),
        (".png", "PNG"),
    ])
    def test_image_extensions(self, tmp_path, ext, expected):
        assert H().detect_type(_f(tmp_path, f"img{ext}", B)) == expected

    @pytest.mark.parametrize("ext, expected", [
        (".mp3", "UNKNOWN"),
        (".mp4", "UNKNOWN"),
        (".mkv", "UNKNOWN"),
        (".mov", "UNKNOWN"),
        (".wav", "UNKNOWN"),
        (".ogg", "UNKNOWN"),
        (".avi", "UNKNOWN"),
    ])
    def test_media_extensions_no_magic(self, tmp_path, ext, expected):
        assert H().detect_type(_f(tmp_path, f"media{ext}", B)) == expected

    def test_pdf_extension_fallback(self, tmp_path):
        assert H().detect_type(_f(tmp_path, "doc.pdf", B)) == "PDF"

    def test_no_extension_unknown(self, tmp_path):
        assert H().detect_type(_f(tmp_path, "noextension", B)) == "UNKNOWN"

    def test_wav_magic_bytes(self, tmp_path):
        assert H().detect_type(_f(tmp_path, "s.wav", Wav)) == "WAV"

    def test_mp4_magic_bytes(self, tmp_path):
        assert H().detect_type(_f(tmp_path, "v.mp4", M)) == "MP4"

    def test_nonexistent_file(self, tmp_path):
        # Use a path with no extension at all to avoid extension fallback
        assert H().detect_type(tmp_path / "nonexistent") == "UNKNOWN"


# ============================================================
# TestCheckSize
# ============================================================

class TestCheckSize:
    def test_under_limit(self, tmp_path):
        assert H(size_limit=1024).check_size(_f(tmp_path, "x", B))

    def test_over_limit(self, tmp_path):
        assert not H(size_limit=10).check_size(_f(tmp_path, "x", b'\x00' * 100))

    def test_exactly_equal(self, tmp_path):
        assert H(size_limit=100).check_size(_f(tmp_path, "x", b'\x00' * 100))

    def test_file_not_found(self, tmp_path):
        pytest.raises(FileNotFoundError, H().check_size, tmp_path / "g")

    def test_exactly_one_byte_over(self, tmp_path):
        data = b'\x00' * 100
        assert not H(size_limit=99).check_size(_f(tmp_path, "y", data))

    def test_exactly_one_byte_under(self, tmp_path):
        assert H(size_limit=101).check_size(_f(tmp_path, "z", b'\x00' * 100))

    def test_zero_limit(self, tmp_path):
        p = _f(tmp_path, "zero.bin", b'\x00' * 10)
        assert not H(size_limit=0).check_size(p)

    def test_zero_byte_file(self, tmp_path):
        p = _f(tmp_path, "empty.bin", b'')
        assert H(size_limit=10).check_size(p)


# ============================================================
# TestProcessImage
# ============================================================

class TestProcessImage:
    def test_png(self, tmp_path):
        r = H().process_image(_f(tmp_path, "t.png", P))
        assert "base64" in r and "mime_type" in r

    def test_jpeg(self, tmp_path):
        r = H().process_image(_f(tmp_path, "t.jpg", J))
        assert r["mime_type"] == "image/jpeg"

    def test_oversized_warning(self, tmp_path):
        h = H(size_limit=5)
        assert "warning" in h.process_image(_f(tmp_path, "t.png", P))

    def test_invalid_image_error(self, tmp_path):
        assert "error" in H().process_image(_f(tmp_path, "t.bin", B))

    def test_gif_image(self, tmp_path):
        r = H().process_image(_f(tmp_path, "t.gif", G))
        assert r["mime_type"] == "image/gif"

    def test_webp_image(self, tmp_path):
        r = H().process_image(_f(tmp_path, "t.webp", Wb))
        assert r["mime_type"] == "image/webp"

    def test_bmp_image(self, tmp_path):
        r = H().process_image(_f(tmp_path, "t.bmp", B))
        assert r["mime_type"] == "image/bmp"

    def test_jpeg_extension_uppercase(self, tmp_path):
        r = H().process_image(_f(tmp_path, "T.JPG", J))
        assert r["mime_type"] == "image/jpeg"

    def test_large_image_resize(self, tmp_path):
        data = J + b'\x00' * 10485761  # ~10 MB + 1 byte
        r = H(size_limit=20971520).process_image(_f(tmp_path, "big.jpg", data))
        assert "base64" in r
        assert "mime_type" in r

    def test_corrupt_image_handling(self, tmp_path):
        data = b'\xff\xd8\xff\xe0' + b'\xff' * 200
        r = H().process_image(_f(tmp_path, "corrupt.jpg", data))
        assert "base64" in r or "error" in r


# ============================================================
# TestProcessPDF
# ============================================================

class TestProcessPDF:
    def test_detect_pdf(self, tmp_path):
        assert H().detect_type(_f(tmp_path, "d.pdf", F)) == "PDF"

    def test_process_pdf_ok(self, tmp_path):
        h = H()
        f = _f(tmp_path, "d.pdf", F)
        assert h.process_pdf(f) is not None

    def test_detect_txt_as_pdf_by_magic(self, tmp_path):
        assert H().detect_type(_f(tmp_path, "d.txt", F)) == "PDF"

    def test_process_pdf_disabled(self, tmp_path):
        h = H(pdf_enabled=False)
        f = _f(tmp_path, "d.pdf", F)
        results = h.process([f])
        assert len(results) == 1
        assert "warning" in results[0] or "PDF disabled" in str(results[0])

    def test_process_pdf_result_structure(self, tmp_path):
        h = H()
        f = _f(tmp_path, "d.pdf", F)
        r = h.process_pdf(f)
        assert "base64" in r
        assert "mime_type" in r
        assert "size" in r
        assert "format" in r

    def test_process_pdf_multipage(self, tmp_path):
        # Simulate a multi-page PDF header with dummy content
        data = (b'%PDF-1.4\n'
                b'1 0 obj\n<<>>\nendobj\n'
                b'2 0 obj\n<<>>\nendobj\n'
                b'xref\n'
                b'trailer\n<<>>\n'
                b'%%EOF')
        h = H()
        f = _f(tmp_path, "multi.pdf", data + b'\x00' * 100)
        r = h.process_pdf(f)
        assert r is not None
        assert "format" in r


# ============================================================
# TestProcessAudioVideo
# ============================================================

class TestProcessAudioVideo:
    def test_mp3(self, tmp_path):
        f = _f(tmp_path, "a.mp3", ID3)
        assert H().process_audio_video(f) is not None

    def test_mp4(self, tmp_path):
        f = _f(tmp_path, "v.mp4", M)
        assert H().process_audio_video(f) is not None

    def test_unknown_format_error(self, tmp_path):
        assert "error" in H().process_audio_video(_f(tmp_path, "x.ogg", B))

    def test_wav_extension(self, tmp_path):
        f = _f(tmp_path, "audio.wav", Wav)
        r = H().process_audio_video(f)
        assert r is not None
        assert r["format"] == "WAV"

    def test_ogg_magic_fallback(self, tmp_path):
        f = _f(tmp_path, "audio.ogg", B)
        r = H().process_audio_video(f)
        assert "error" in r

    def test_webm_format(self, tmp_path):
        data = b'\x1a\x45\xdf\xa3' + b'\x00' * 50
        f = _f(tmp_path, "vid.webm", data)
        r = H().process_audio_video(f)
        assert r is not None
        assert r["format"] == "WEBM"

    def test_mkv_extension(self, tmp_path):
        # .mkv with valid magic bytes (WebM-like) falls through to detect MBV
        data = b'\x1a\x45\xdf\xa3' + b'\x00' * 50
        f = _f(tmp_path, "vid.mkv", data)
        r = H().process_audio_video(f)
        assert r is not None
        assert r["format"] == "WEBM"

    def test_mov_extension(self, tmp_path):
        # .mov with MP4-like magic (ftyp) is detected as MP4 format
        f = _f(tmp_path, "vid.mov", M)
        r = H().process_audio_video(f)
        assert r is not None
        assert r["format"] == "MP4"

    def test_flac_magic(self, tmp_path):
        data = b'fLaC' + b'\x00' * 50
        f = _f(tmp_path, "audio.flac", data)
        r = H().process_audio_video(f)
        assert r is not None
        assert r["format"] == "FLAC"


# ============================================================
# TestProcess (batch)
# ============================================================

class TestProcess:
    def test_single_file(self, tmp_path):
        r = H().process([_f(tmp_path, "a.png", P)])
        assert len(r) == 1 and r[0] is not None

    def test_multiple_files(self, tmp_path):
        r = H().process([_f(tmp_path, "a.png", P), _f(tmp_path, "b.jpg", J)])
        assert len(r) == 2

    def test_invalid_file_handled(self, tmp_path):
        r = H().process([_f(tmp_path, "a.png", P), _f(tmp_path, "b.bin", B)])
        assert len(r) == 2

    def test_mixed_types(self, tmp_path):
        r = H().process([
            _f(tmp_path, "a.png", P),
            _f(tmp_path, "b.jpg", J),
            _f(tmp_path, "c.pdf", F),
            _f(tmp_path, "d.mp3", ID3),
            _f(tmp_path, "e.mp4", M),
        ])
        assert len(r) == 5
        for entry in r:
            assert entry is not None

    def test_missing_file(self, tmp_path):
        r = H().process([_f(tmp_path, "a.png", P),
                         tmp_path / "nonexistent.png"])
        assert len(r) == 2
        assert "error" in r[1]
        assert "file not found" in r[1]["error"]

    def test_empty_list(self, tmp_path):
        r = H().process([])
        assert r == []

    def test_pdf_disabled_in_batch(self, tmp_path):
        h = H(pdf_enabled=False)
        r = h.process([_f(tmp_path, "a.png", P), _f(tmp_path, "b.pdf", F)])
        assert len(r) == 2
        pdf_r = r[1]
        assert "warning" in pdf_r or "disabled" in str(pdf_r)

    def test_all_unknown_types(self, tmp_path):
        r = H().process([_f(tmp_path, "a.bin", B), _f(tmp_path, "b.dat", B)])
        assert len(r) == 2
        for entry in r:
            assert "error" in entry

    def test_webm_in_batch(self, tmp_path):
        data = b'\x1a\x45\xdf\xa3' + b'\x00' * 50
        r = H().process([_f(tmp_path, "v.webm", data)])
        assert len(r) == 1
        assert r[0] is not None


# ============================================================
# TestToGeminiFormat
# ============================================================

class TestToGeminiFormat:
    def test_single_image(self, tmp_path):
        h = H()
        g = h.to_gemini_format(h.process_image(_f(tmp_path, "x.png", P)))
        d = g["inline_data"]
        assert "inline_data" in g and "mime_type" in d and "data" in d

    def test_jpeg_mime_type(self, tmp_path):
        h = H()
        g = h.to_gemini_format(h.process_image(_f(tmp_path, "x.jpg", J)))
        assert g["inline_data"]["mime_type"] == "image/jpeg"

    def test_non_empty_data(self, tmp_path):
        h = H()
        g = h.to_gemini_format(h.process_image(_f(tmp_path, "x.png", P)))
        assert len(g["inline_data"]["data"]) > 0

    def test_dict_conversion(self, tmp_path):
        h = H()
        d = {"mime_type": "image/png", "base64": "dGVzdA=="}
        g = h.to_gemini_format(d)
        assert g["inline_data"]["mime_type"] == "image/png"
        assert g["inline_data"]["data"] == "dGVzdA=="

    def test_empty_dict(self, tmp_path):
        h = H()
        g = h.to_gemini_format({})
        assert g["inline_data"]["mime_type"] == ""
        assert g["inline_data"]["data"] == ""

    def test_media_input_object(self, tmp_path):
        from loop_antigravity.multimodal_handler import MediaInput
        mi = MediaInput(
            "/fake/path.png", "IMAGE", "PNG", 100, "image/png", "ZmFrZQ=="
        )
        h = H()
        g = h.to_gemini_format(mi)
        assert g["inline_data"]["mime_type"] == "image/png"
        assert g["inline_data"]["data"] == "ZmFrZQ=="

    def test_media_input_no_base64(self, tmp_path):
        from loop_antigravity.multimodal_handler import MediaInput
        mi = MediaInput(
            "/fake/path.png", "IMAGE", "PNG", 100, "image/png"
        )
        h = H()
        g = h.to_gemini_format(mi)
        assert g["inline_data"]["data"] == ""


# ============================================================
# TestMediaTypeEnum
# ============================================================

class TestMediaTypeEnum:
    def test_constants_exist(self):
        from loop_antigravity.multimodal_handler import MediaType
        assert MediaType.IMAGE == "IMAGE"
        assert MediaType.PDF == "PDF"
        assert MediaType.AUDIO == "AUDIO"
        assert MediaType.VIDEO == "VIDEO"
        assert MediaType.UNKNOWN == "UNKNOWN"

    def test_all_values_unique(self):
        from loop_antigravity.multimodal_handler import MediaType
        values = [MediaType.IMAGE, MediaType.PDF, MediaType.AUDIO,
                  MediaType.VIDEO, MediaType.UNKNOWN]
        assert len(values) == len(set(values))


# ============================================================
# TestSizeLimitEdgeCases
# ============================================================

class TestSizeLimitEdgeCases:
    def test_very_large_limit(self, tmp_path):
        h = H(size_limit=10_737_418_240)  # 10 GB
        assert h.check_size(_f(tmp_path, "small.bin", b'\x00' * 100))

    def test_zero_limit_behavior(self, tmp_path):
        h = H(size_limit=0)
        assert not h.check_size(_f(tmp_path, "tiny.bin", b'\x01'))

    def test_zero_limit_process_image(self, tmp_path):
        h = H(size_limit=0)
        r = h.process_image(_f(tmp_path, "t.png", P))
        assert "warning" in r or "error" in r

    def test_negative_limit_raises(self, tmp_path):
        with pytest.raises(ValueError):
            H(size_limit=-1)

    def test_negative_limit_raises_message(self, tmp_path):
        with pytest.raises(ValueError, match="size_limit"):
            H(size_limit=-5)

    def test_process_with_size_limit_near_file_boundary(self, tmp_path):
        data = b'\xff\xd8\xff\xe0' + b'\x00' * 50
        f = _f(tmp_path, "near.jpg", data)
        h = H(size_limit=len(data))
        r = h.process_image(f)
        assert "base64" in r or "error" in r


# ============================================================
# TestProcessImageException
# ============================================================

class TestProcessImageException:
    def test_process_image_io_error(self, tmp_path):
        """process_image 外层异常处理应捕获 IO 错误。"""
        from unittest import mock
        h = H()
        f = _f(tmp_path, "e.png", P)
        with mock.patch("builtins.open", side_effect=OSError("disk full")):
            r = h.process_image(f)
            assert "error" in r


# ============================================================
# TestProcessPdfException
# ============================================================

class TestProcessPdfException:
    def test_process_pdf_io_error(self, tmp_path):
        """process_pdf 外层异常处理应捕获 IO 错误。"""
        from unittest import mock
        h = H()
        f = _f(tmp_path, "e.pdf", F)
        with mock.patch("builtins.open", side_effect=OSError("disk full")):
            r = h.process_pdf(f)
            assert "error" in r

    def test_process_pdf_with_pypdf2_mock(self, tmp_path):
        """模拟 PyPDF2 可用时的分页检测。"""
        from unittest import mock
        h = H()
        f = _f(tmp_path, "e.pdf", F)

        # Mock PdfReader 来模拟多页 PDF
        fake_reader = mock.MagicMock()
        fake_reader.pages = [1, 2, 3]  # 3 pages
        fake_pdf_class = mock.MagicMock(return_value=fake_reader)

        with mock.patch.dict("sys.modules", {"PyPDF2": mock.MagicMock(
            PdfReader=fake_pdf_class
        )}):
            # Need to reload the handler to get _PDF=1
            import importlib
            import loop_antigravity.multimodal_handler as mh
            importlib.reload(mh)
            # Override _PDF to simulate PyPDF2 being available
            old_pdf = mh._PDF
            mh._PDF = 1
            try:
                r = h.process_pdf(f)
                assert r is not None
                assert "format" in r
                assert "PDF" in r["format"]
            finally:
                mh._PDF = old_pdf


class TestPILImportHandling:
    """测试 PIL 不可用时的回退行为。"""

    def test_pil_import_failure(self, monkeypatch):
        """模拟 PIL 导入失败，验证 _PIL=0 路径。"""
        # Remove PIL from sys.modules to simulate import failure
        import sys
        saved_pil = sys.modules.pop("PIL", None)
        saved_image = sys.modules.pop("PIL.Image", None)
        saved_pillow = sys.modules.pop("PIL.Image", None)
        try:
            # Add a hook to block PIL import
            import builtins
            original_import = builtins.__import__

            def blocked_import(name, *args, **kwargs):
                if name == "PIL" or name.startswith("PIL."):
                    raise ImportError("No module named PIL")
                return original_import(name, *args, **kwargs)

            monkeypatch.setattr("builtins.__import__", blocked_import)
            # Force re-evaluation by creating a new handler via reload
            import importlib
            import loop_antigravity.multimodal_handler as mh
            importlib.reload(mh)
            # The module-level _PIL should now be 0
            assert mh._PIL == 0
        finally:
            if saved_pil is not None:
                sys.modules["PIL"] = saved_pil
            if saved_image is not None:
                sys.modules["PIL.Image"] = saved_image


# ============================================================
# TestRmException
# ============================================================

class TestRmException:
    def test_rm_oserror_returns_empty(self, tmp_path):
        """_rm 遇到 OSError 应返回空 bytes。"""
        h = H()
        result = h._rm(str(tmp_path / "nonexistent_file.xyz"), 12)
        assert result == b""
        result = h._rm(str(tmp_path / "nonexistent.abc"))
        assert result == b""


class TestPdfReaderFallback:
    """PDF 页数检测异常回退测试。"""

    def test_pdfreader_exception_fallback(self, tmp_path):
        """PdfReader 抛出异常时 process_pdf 应回退到纯 'PDF' 格式。"""
        from unittest import mock
        import loop_antigravity.multimodal_handler as mh

        h = H()
        f = _f(tmp_path, "e.pdf", F)

        old_pdf = mh._PDF
        mh._PDF = 1  # 模拟 PyPDF2 可用

        # Mock PdfReader 抛出异常
        fake_pdfreader = mock.MagicMock(side_effect=RuntimeError("bad PDF"))
        with mock.patch("loop_antigravity.multimodal_handler.PdfReader",
                        fake_pdfreader):
            try:
                r = h.process_pdf(f)
                assert r is not None
                assert r["format"] == "PDF"
            finally:
                mh._PDF = old_pdf


class TestPILThumbnailPath:
    """PIL 缩略图处理路径测试。"""

    def test_large_image_thumbnail(self, tmp_path):
        """process_image 对大图片(>10MB)应走 PIL 缩略图路径。"""
        from unittest import mock
        import loop_antigravity.multimodal_handler as mh

        # 确保 PIL 可用
        old_pil = mh._PIL
        mh._PIL = 1

        # 创建一个有效的 JPEG 文件
        f = _f(tmp_path, "big.jpg", J + b'\x00' * 100)

        # Mock os.path.getsize 返回大于 10MB
        mock_img = mock.MagicMock()
        mock_img.thumbnail = mock.MagicMock()
        mock_img.convert = mock.MagicMock(return_value=mock_img)
        mock_img.save = mock.MagicMock()

        with mock.patch("os.path.getsize", return_value=11_000_000), \
             mock.patch("loop_antigravity.multimodal_handler.Image.open",
                        return_value=mock_img):
            try:
                h = H(size_limit=20_000_000)
                r = h.process_image(f)
                assert "base64" in r
                # 确保走的是缩略图路径
                assert mock_img.thumbnail.called or mock_img.convert.called
            finally:
                mh._PIL = old_pil
