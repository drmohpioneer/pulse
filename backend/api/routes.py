from datetime import datetime

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from backend.ai.copilot import CopilotResponse
from backend.services.demo_workflow import (
    DemoCorrectionActionRequest,
    DemoEventRequest,
    DemoConfirmationActionRequest,
    DemoUndoRequest,
    DemoScenarioResponse,
    DemoStateResponse,
    DemoTranscriptRequest,
    DemoTranscriptResponse,
    LiveAudioChunkRequest,
    LiveAudioChunkResponse,
    LiveAudioSessionActionRequest,
    LiveAudioSessionResponse,
    LiveAudioSessionStartRequest,
    LiveAudioUploadRequest,
    LiveScriptedStreamResponse,
    LiveTranscriptChunkRequest,
    LiveTranscriptChunkResponse,
    LiveVoiceSessionActionRequest,
    LiveVoiceSessionResponse,
    LiveVoiceSessionStartRequest,
    demo_session,
)

router = APIRouter(prefix="/api")


# TODO: Add authenticated, validated API routes after workflow contracts are accepted.
# TODO: Keep frontend-facing routes thin; delegate all business rules to backend services.


@router.get("/demo", response_model=DemoStateResponse)
def get_demo_state() -> DemoStateResponse:
    return demo_session.current_state()


@router.post("/demo/reset", response_model=DemoStateResponse)
def reset_demo_state() -> DemoStateResponse:
    return demo_session.reset()


@router.post("/demo/events", response_model=DemoStateResponse)
def create_demo_event(request: DemoEventRequest) -> DemoStateResponse:
    return demo_session.process_action(request.action)


@router.post("/demo/transcripts", response_model=DemoTranscriptResponse)
def create_demo_transcript(request: DemoTranscriptRequest) -> DemoTranscriptResponse:
    return demo_session.process_transcript(request)


@router.post("/demo/live/start", response_model=LiveVoiceSessionResponse)
def start_live_voice_session(
    request: LiveVoiceSessionStartRequest,
) -> LiveVoiceSessionResponse:
    return demo_session.start_live_voice_session(request)


@router.post("/demo/live/stop", response_model=LiveVoiceSessionResponse)
def stop_live_voice_session(
    request: LiveVoiceSessionActionRequest,
) -> LiveVoiceSessionResponse:
    try:
        return demo_session.stop_live_voice_session(request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/demo/live/chunks", response_model=LiveTranscriptChunkResponse)
def ingest_live_transcript_chunk(
    request: LiveTranscriptChunkRequest,
) -> LiveTranscriptChunkResponse:
    try:
        return demo_session.ingest_live_transcript_chunk(request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/demo/live/scripted/next", response_model=LiveScriptedStreamResponse)
def advance_scripted_live_stream(
    request: LiveVoiceSessionActionRequest,
) -> LiveScriptedStreamResponse:
    try:
        return demo_session.advance_scripted_live_stream(request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/demo/live-audio/start", response_model=LiveAudioSessionResponse)
def start_live_audio_session(
    request: LiveAudioSessionStartRequest,
) -> LiveAudioSessionResponse:
    try:
        return demo_session.start_live_audio_session(request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/demo/live-audio/stop", response_model=LiveAudioSessionResponse)
def stop_live_audio_session(
    request: LiveAudioSessionActionRequest,
) -> LiveAudioSessionResponse:
    try:
        return demo_session.stop_live_audio_session(request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/demo/live-audio/chunks", response_model=LiveAudioChunkResponse)
def ingest_live_audio_chunk(
    request: LiveAudioChunkRequest,
) -> LiveAudioChunkResponse:
    try:
        return demo_session.ingest_live_audio_chunk(request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/demo/live-audio/uploads", response_model=LiveAudioChunkResponse)
async def upload_live_audio_chunk(
    session_id: str = Form(...),
    sequence: int = Form(...),
    timestamp: datetime | None = Form(None),
    content_type: str | None = Form(None),
    audio: UploadFile = File(...),
) -> LiveAudioChunkResponse:
    try:
        content = await audio.read()
        return demo_session.ingest_live_audio_upload(
            LiveAudioUploadRequest(
                session_id=session_id,
                sequence=sequence,
                content_type=content_type or audio.content_type or "application/octet-stream",
                content=content,
                timestamp=timestamp,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/demo/confirmations/confirm", response_model=DemoTranscriptResponse)
def confirm_demo_candidate(
    request: DemoConfirmationActionRequest,
) -> DemoTranscriptResponse:
    try:
        return demo_session.confirm_voice_candidate(request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/demo/confirmations/reject", response_model=DemoTranscriptResponse)
def reject_demo_candidate(
    request: DemoConfirmationActionRequest,
) -> DemoTranscriptResponse:
    try:
        return demo_session.reject_voice_candidate(request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/demo/confirmations/correct", response_model=DemoTranscriptResponse)
def correct_demo_candidate(
    request: DemoCorrectionActionRequest,
) -> DemoTranscriptResponse:
    try:
        return demo_session.correct_voice_candidate(request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/demo/auto-accepted/undo", response_model=DemoTranscriptResponse)
def undo_auto_accepted_demo_event(
    request: DemoUndoRequest,
) -> DemoTranscriptResponse:
    try:
        return demo_session.undo_auto_accepted_event(request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/demo/scenario/end-to-end", response_model=DemoScenarioResponse)
def run_end_to_end_demo_scenario() -> DemoScenarioResponse:
    return demo_session.run_end_to_end_voice_scenario()


@router.get("/demo/copilot", response_model=CopilotResponse)
def get_demo_copilot_note() -> CopilotResponse:
    return demo_session.copilot_note()
