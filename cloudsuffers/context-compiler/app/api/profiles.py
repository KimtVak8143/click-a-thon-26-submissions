from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import get_source_profiler
from app.profiling.models import SourceProfile, StrictModel
from app.profiling.profiler import SourceProfiler

router = APIRouter(tags=["profiles"])


class ProfileErrorDetail(StrictModel):
    code: str
    message: str


class ProfileErrorResponse(StrictModel):
    detail: ProfileErrorDetail


@router.post(
    "/profiles",
    response_model=SourceProfile,
    status_code=status.HTTP_200_OK,
    responses={status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ProfileErrorResponse}},
)
async def create_profile(
    request: Request,
    events: UploadFile,
    profiler: Annotated[SourceProfiler, Depends(get_source_profiler)],
) -> SourceProfile:
    filename = events.filename
    if (
        not filename
        or "\\" in filename
        or Path(filename).name != filename
        or Path(filename).suffix.lower() != ".ndjson"
    ):
        _unprocessable("invalid_filename", "events must have a safe .ndjson filename")

    settings = request.app.state.settings
    maximum_size = settings.profile_max_upload_bytes
    if events.size is not None and events.size > maximum_size:
        _unprocessable("file_too_large", f"events file exceeds the {maximum_size}-byte limit")

    temporary_path: Path | None = None
    uploaded_bytes = 0
    try:
        with NamedTemporaryFile(mode="wb", suffix=".ndjson", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            while chunk := await events.read(settings.profile_upload_chunk_bytes):
                uploaded_bytes += len(chunk)
                if uploaded_bytes > maximum_size:
                    _unprocessable(
                        "file_too_large",
                        f"events file exceeds the {maximum_size}-byte limit",
                    )
                temporary.write(chunk)

        profile = await run_in_threadpool(profiler.profile, temporary_path)
        if profile.file.valid_row_count == 0:
            _unprocessable("no_valid_rows", "events file contains no valid JSON object rows")
        if profile.file.malformed_row_count:
            _unprocessable(
                "malformed_ndjson",
                f"events file contains {profile.file.malformed_row_count} malformed row(s)",
            )
        return profile
    finally:
        await events.close()
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _unprocessable(code: str, message: str) -> None:
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": code, "message": message},
    )
