from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.temporal.activities import (
        ClipJobInput,
        inspect_and_extract_audio,
        transcribe_audio,
        detect_viral_highlights,
        render_viral_clips,
    )


@workflow.defn
class VideoClippingWorkflow:
    @workflow.run
    async def run(self, job: ClipJobInput) -> dict:
        retry = RetryPolicy(
            maximum_attempts=3,
            initial_interval=timedelta(seconds=3),
        )

        media_info = await workflow.execute_activity(
            inspect_and_extract_audio,
            job,
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=retry,
        )

        transcript_info = await workflow.execute_activity(
            transcribe_audio,
            media_info,
            start_to_close_timeout=timedelta(minutes=20),
            retry_policy=retry,
        )

        highlights_info = await workflow.execute_activity(
            detect_viral_highlights,
            {"media_info": media_info, "transcript_info": transcript_info},
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=retry,
        )

        render_info = await workflow.execute_activity(
            render_viral_clips,
            {
                "media_info": media_info,
                "transcript_info": transcript_info,
                "highlights_info": highlights_info,
            },
            start_to_close_timeout=timedelta(minutes=20),
            retry_policy=retry,
        )

        return {
            "workflow_status": "completed",
            "project_id": job.project_id,
            "rendered_clips": render_info["rendered_clips"],
            "manifest_path": render_info["manifest_path"],
        }
