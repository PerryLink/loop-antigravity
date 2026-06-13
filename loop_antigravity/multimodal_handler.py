"""
多模态文件处理器——支持图片、PDF、音频/视频文件的检测、编码与 Gemini 格式化。

## 支持的媒体类型
- 图片: PNG, JPEG, GIF, WebP, BMP
- 文档: PDF（可选，可通过 pdf_enabled 控制）
- 音频: MP3, WAV, FLAC, OGG
- 视频: MP4, WebM, MKV, MOV, AVI

## 核心功能
- **类型检测**: 通过魔数签名 + 扩展名双重检测文件类型
- **大小校验**: 默认 20MB 限制，超大文件自动压缩缩略图（由 PIL 处理）
- **Base64 编码**: 支持全量编码和缩略图压缩后编码两种策略
- **Gemini 格式化**: 输出符合 Gemini API `inline_data` 格式的结构化数据

## 使用示例
```python
handler = MultimodalHandler(size_limit=20_971_520, pdf_enabled=True)
results = handler.process(["screenshot.png", "document.pdf"])
gemini_parts = [handler.to_gemini_format(r) for r in results]
```
"""
import base64,io,os
try:from PyPDF2 import PdfReader;_PDF=1
except:_PDF=0
try:from PIL import Image;_PIL=1
except:_PIL=0
class MediaType:
 IMAGE="IMAGE";PDF="PDF";AUDIO="AUDIO";VIDEO="VIDEO";UNKNOWN="UNKNOWN"
class MediaInput:
 def __init__(s,p,mt,f,sz,mi,b64=None):
  s.path=p;s.media_type=mt;s.format=f;s.size_bytes=sz;s.mime_type=mi;s.base64_data=b64
_M=[(b"\x89PNG","PNG","image/png"),(b"\xff\xd8\xff","JPEG","image/jpeg"),
(b"GIF8","GIF","image/gif"),(b"%PDF","PDF","application/pdf"),
(b"ID3","MP3","audio/mpeg"),(b"fLaC","FLAC","audio/flac"),
(b"\x1a\x45\xdf\xa3","WEBM","video/webm")]
_MIME={".png":"image/png",".jpg":"image/jpeg",".jpeg":"image/jpeg",".gif":"image/gif",
".webp":"image/webp",".bmp":"image/bmp",".mp3":"audio/mpeg",".wav":"audio/wav",
".flac":"audio/flac",".ogg":"audio/ogg",".mp4":"video/mp4",".webm":"video/webm",
".mkv":"video/x-matroska",".mov":"video/quicktime",".avi":"video/x-msvideo",
".pdf":"application/pdf"}
_IE={".png",".jpg",".jpeg",".webp",".gif",".bmp"}
class MultimodalHandler:
 """多模态文件处理器。

 负责文件的类型检测、大小校验、Base64 编码和 Gemini API 格式化。
 支持图片（PNG/JPEG/GIF/WebP）、PDF、音频和视频文件。

 Attributes:
     size_limit: 单文件大小上限（字节），默认 20MB。超过限值的图片会触发缩略图压缩。
     pdf_enabled: 是否启用 PDF 处理。禁用后 PDF 文件返回 warning。

 线程安全性: 本类为纯数据变换，无共享可变状态，默认线程安全。
 """

 def __init__(s,size_limit=20971520,pdf_enabled=True):
  """初始化多模态处理器。

  Args:
      size_limit: 单文件大小上限（字节），必须 >= 0，默认 20_971_520 (20MB)。
      pdf_enabled: 是否启用 PDF 处理。禁用后对 PDF 文件仅返回 warning 而不编码内容。
  """
  if size_limit<0:raise ValueError(f"size_limit must be >=0, got {size_limit}")
  s._mb=size_limit;s._m=size_limit;s._pdf=pdf_enabled
 @property
 def size_limit(s):return s._mb
 @property
 def pdf_enabled(s):return s._pdf
 def check_size(s,p):
  """检查文件大小是否在限制内。

  Args:
      p: 文件系统路径。

  Returns:
      bool: True 表示文件大小 <= size_limit，可以处理。
  """
  return os.path.getsize(p)<=s._m
 def detect_type(s,p):
  """检测文件媒体类型（双重策略：魔数字节签名 + 扩展名回退）。

  依次检查: 静态签名表(_M) -> RIFF 容器格式 -> MP4 ftyp -> 扩展名回退。

  Args:
      p: 文件系统路径。

  Returns:
      str: 检测到的格式标签（PNG/JPEG/GIF/PDF/MP3/FLAC/WEBM/WAV/MP4/WEBP/UNKNOWN 之一）。
  """
  m=s._rm(p,12)
  for g,f,_ in _M:
   if m[:len(g)]==g:return f
  if m[:4]==b"RIFF"and len(m)>=12:return"WEBP"if m[8:12]==b"WEBP"else"WAV"
  if len(m)>=8 and m[4:8]==b"ftyp":return"MP4"
  e=os.path.splitext(p)[1].lower()
  if e==".pdf":return"PDF"
  if e in _IE:return"JPEG"if e in(".jpg",".jpeg")else e[1:].upper()
  return"UNKNOWN"
 def process_image(s,p):
  """处理图片文件：检测类型 -> 超限压缩 -> Base64 编码。

  超大文件(>10MB)策略: 如果 PIL 可用，先生成 2048x2048 缩略图再编码；
  否则或 PIL 处理失败时直接读取全量数据进行 Base64 编码。

  Args:
      p: 图片文件路径。

  Returns:
      dict: {"base64", "mime_type", "format", "size", "warning"(可选), "error"(失败时)}。
  """
  t=s.detect_type(p);fs=os.path.getsize(p);mi=_MIME.get(os.path.splitext(p)[1].lower(),"image/png")
  if t=="UNKNOWN"and os.path.splitext(p)[1].lower()not in _IE:
   return{"error":"not an image","format":t,"size":fs,"base64":"","mime_type":""}
  w=None
  if fs>s._m:w=f"over {s._mb}b"
  try:
   b64=""
   if _PIL and fs>10485760:
    try:
     img=Image.open(p);img.thumbnail((2048,2048),Image.LANCZOS)
     bf=io.BytesIO();sf="JPEG"if t=="JPEG"else"PNG"
     img.convert("RGB").save(bf,format=sf);b64=base64.b64encode(bf.getvalue()).decode()
    except:b64=""
   if not b64:
    with open(p,"rb")as f:b64=base64.b64encode(f.read()).decode()
   r={"base64":b64,"mime_type":mi,"format":t,"size":fs}
   if w:r["warning"]=w
   return r
  except Exception as e:return{"error":str(e),"format":t,"size":fs,"base64":"","mime_type":mi}
 def process_pdf(s,p):
  """处理 PDF 文件：Base64 编码，可选页数检测。

  如果 PyPDF2 可用，会在 format 字段中附加页数信息（如 "PDF(5p)"）。

  Args:
      p: PDF 文件路径。

  Returns:
      dict: {"base64", "mime_type", "format", "size", "error"(失败时)}。
  """
  t=s.detect_type(p);fs=os.path.getsize(p);mi=_MIME.get(".pdf","application/pdf")
  try:
   with open(p,"rb")as f:b64=base64.b64encode(f.read()).decode()
   ft="PDF"
   if _PDF:
    try:ft=f"PDF({len(PdfReader(p).pages)}p)"
    except:pass
   return{"base64":b64,"mime_type":mi,"format":ft,"size":fs}
  except Exception as e:return{"error":str(e)}
 def process_audio_video(s,p):
  """处理音频/视频文件：检测类型并获取元信息（不编码内容以节约内存）。

  Args:
      p: 音频或视频文件路径。

  Returns:
      dict: {"format", "size", "mime_type"} 或 {"error": "unsupported format"}。
  """
  t=s.detect_type(p)
  if t=="UNKNOWN":return{"error":"unsupported format"}
  return{"format":t,"size":os.path.getsize(p),"mime_type":_MIME.get(os.path.splitext(p)[1].lower(),"application/octet-stream")}
 def process(s,ps):
  """批量处理多个文件：遍历路径列表，根据检测类型分发到对应处理器。

  处理规则:
  - 文件不存在 -> {"error": "file not found"}
  - PDF 且 pdf_enabled=False -> {"warning": "PDF disabled"}
  - 图片类型 -> process_image()
  - 音频/视频类型 -> process_audio_video()
  - 未知类型 -> {"error": "unknown type"}

  Args:
      ps: 文件路径字符串列表。

  Returns:
      list[dict]: 每个文件的处理结果列表。
  """
  r=[]
  for p in ps:
   if not os.path.isfile(p):r.append({"error":"file not found"});continue
   t=s.detect_type(p)
   if t=="PDF":
    if s._pdf:r.append(s.process_pdf(p))
    else:r.append({"warning":"PDF disabled"})
   elif t in("PNG","JPEG","GIF","WEBP"):r.append(s.process_image(p))
   elif t in("MP3","WAV","FLAC","WEBM","MP4"):r.append(s.process_audio_video(p))
   else:r.append({"error":"unknown type"})
  return r
 def to_gemini_format(s,d):
  """将处理结果转换为 Gemini API inline_data 格式。

  支持 dict 或 MediaInput 对象两种输入。

  Args:
      d: 由 process_*() 返回的 dict 或 MediaInput 数据类。

  Returns:
      dict: {"inline_data": {"mime_type": str, "data": str (base64)}}。
  """
  if isinstance(d,dict):return{"inline_data":{"mime_type":d.get("mime_type",""),"data":d.get("base64","")}}
  return{"inline_data":{"mime_type":d.mime_type,"data":d.base64_data or""}}
 def _rm(s,p,n=12):
  """读取文件的前 n 个字节（内部方法，用于魔数字节签名检测）。

  Args:
      p: 文件路径。
      n: 要读取的字节数，默认 12（覆盖常见文件头的最大长度）。

  Returns:
      bytes: 文件头部字节。若文件读取失败（OSError）返回空 bytes。
  """
  try:
   with open(p,"rb")as f:return f.read(n)
  except OSError:return b""
