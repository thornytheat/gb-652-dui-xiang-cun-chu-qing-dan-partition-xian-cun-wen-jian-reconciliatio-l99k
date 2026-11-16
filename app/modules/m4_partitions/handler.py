from urllib.parse import parse_qs, quote

from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.errors import DomainError
from .service import execute, get_batch, import_manifest, latest, replay

router = APIRouter(tags=["M4-分区判定"])


def _page_data(request: Request, batch: str | None):
    store = request.app.state.store
    return get_batch(batch, store) if batch else latest(store)


def _render(request: Request, data, msg=None, status_code=200):
    return request.app.state.templates.TemplateResponse(
        request, "m4_partitions.html",
        {"title": "分区判定", "data": data, "msg": msg},
        status_code=status_code,
    )


async def _form(request: Request) -> dict:
    raw = await request.body()
    return _parse_body(request.headers.get("content-type", ""), raw)


def _parse_body(content_type: str, raw: bytes) -> dict:
    """解析 urlencoded 或 multipart 表单，避免额外运行依赖。"""
    if content_type.startswith("multipart/form-data"):
        return _parse_multipart(content_type, raw)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DomainError("INVALID_ENCODING", "表单不是有效的 UTF-8 文本") from exc
    parsed = parse_qs(text, keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}


def _parse_multipart(content_type: str, raw: bytes) -> dict:
    marker = "boundary="
    if marker not in content_type:
        return {}
    boundary = content_type.split(marker, 1)[1].split(";")[0].strip().strip('"')
    if not boundary:
        return {}
    delimiter = b"--" + boundary.encode("latin-1")
    fields = {}
    for part in raw.split(delimiter)[1:]:
        if part.startswith(b"--"):
            break
        # 每个分片以 CRLF 开头，以 CRLF 结束；只去掉这两个分隔用的换行，
        # 保留内容自身的行结构（含尾部空行）。
        if part.startswith(b"\r\n"):
            part = part[2:]
        elif part.startswith(b"\n"):
            part = part[1:]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        elif part.endswith(b"\n"):
            part = part[:-1]
        head, found, body = part.partition(b"\r\n\r\n")
        if not found:
            head, found, body = part.partition(b"\n\n")
        if not found:
            continue
        disposition = ""
        for line in head.decode("utf-8", errors="replace").splitlines():
            if line.lower().startswith("content-disposition:"):
                disposition = line
                break
        name = _disposition_value(disposition, "name")
        if name is None:
            continue
        filename = _disposition_value(disposition, "filename")
        if filename:
            fields[name + "__filename"] = filename
        fields[name] = body.decode("utf-8")
    return fields


def _disposition_value(disposition: str, key: str) -> str | None:
    for part in disposition.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        left, right = part.split("=", 1)
        if left.strip().lower() == key:
            return right.strip().strip('"')
    return None


@router.get("/partitions", response_class=HTMLResponse)
def page(request: Request, batch: str | None = None, msg: str | None = None):
    try:
        data = _page_data(request, batch)
    except DomainError:
        # 失效的批次链接退回最近批次，并把原因显示出来。
        data = _page_data(request, None)
        msg = msg or f"批次不存在：{batch}"
    return _render(request, data, msg)


@router.post("/partitions/import")
async def ui_import(request: Request):
    form = await _form(request)
    uploaded = form.get("manifest_file", "").strip()
    text = uploaded or form.get("text", "")
    filename = form.get("manifest_file__filename", "")
    fmt = form.get("format", "auto")
    if fmt == "auto" and filename.lower().endswith(".jsonl"):
        fmt = "jsonl"
    if fmt == "auto" and filename.lower().endswith(".csv"):
        fmt = "csv"
    payload = {"text": text, "format": fmt, "source": form.get("source", "supplier")}
    objects = form.get("objects", "")
    if objects.strip():
        payload["objects"] = objects.splitlines()
    if form.get("expected_hours", "").strip():
        payload["expected_hours"] = form["expected_hours"].replace(",", " ").split()
    try:
        result = import_manifest(payload, request.app.state.store)
        if not result.get("input_count"):
            raise ValueError("清单没有可核对的行")
    except (DomainError, ValueError, UnicodeDecodeError) as exc:
        message = exc.message if isinstance(exc, DomainError) else str(exc)
        return _render(request, _page_data(request, None), message, status_code=422)
    return RedirectResponse(f"/partitions?batch={result['batch_id']}", status_code=303)


@router.post("/partitions/{batch_id}/replay")
async def ui_replay(request: Request, batch_id: str):
    form = await _form(request)
    payload = {}
    if form.get("row_id"):
        payload["row_id"] = form["row_id"]
    if form.get("target"):
        payload["target"] = form["target"]
    if form.get("source"):
        payload["source"] = form["source"]
    try:
        replay(batch_id, payload, request.app.state.store)
    except DomainError as exc:
        return RedirectResponse(
            f"/partitions?batch={batch_id}&msg={quote(exc.message, safe='')}",
            status_code=303,
        )
    return RedirectResponse(f"/partitions?batch={batch_id}", status_code=303)


@router.post("/api/partitions/import")
def api_import(request: Request, payload: dict = Body(default={})):
    return import_manifest(payload or {}, request.app.state.store)


@router.post("/api/partitions/{batch_id}/replay")
def api_replay(request: Request, batch_id: str, payload: dict = Body(default={})):
    return replay(batch_id, payload or {}, request.app.state.store)


@router.get("/api/partitions/{batch_id}")
def api_batch(request: Request, batch_id: str):
    return get_batch(batch_id, request.app.state.store)


@router.get("/api/partitions")
def api_latest(request: Request, batch: str | None = None):
    store = request.app.state.store
    return get_batch(batch, store) if batch else latest(store)


@router.post("/api/partitions")
def api(request: Request, payload: dict = Body(default={})):
    return execute(payload or {}, request.app.state.store)
