import asyncio
from temporalio.client import Client
from temporalio.worker import Worker
from app.temporal.activities import (
    inspect_and_extract_audio,
    transcribe_audio,
    detect_viral_highlights,
    render_viral_clips,
)
from app.temporal.workflows import VideoClippingWorkflow

TASK_QUEUE = "ai-clipping-queue"


async def main() -> None:
    client = await Client.connect("127.0.0.1:7233")
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[VideoClippingWorkflow],
        activities=[
            inspect_and_extract_audio,
            transcribe_audio,
            detect_viral_highlights,
            render_viral_clips,
        ],
    )
    print(f"Temporal Worker listening on task queue: {TASK_QUEUE}")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
