"""Merge status updates with text, cancelling pending work on close."""
import asyncio


async def text_and_progress(stream, progress):
    iterator = stream.__aiter__()
    next_chunk = asyncio.create_task(anext(iterator))
    next_status = asyncio.create_task(progress.get())
    try:
        while True:
            ready, _ = await asyncio.wait({next_chunk, next_status}, return_when=asyncio.FIRST_COMPLETED)
            if next_status in ready:
                yield 'message_progress', {'message': next_status.result()}
                next_status = asyncio.create_task(progress.get())
            if next_chunk in ready:
                try:
                    text = next_chunk.result()
                except StopAsyncIteration:
                    break
                yield 'message_delta', {'text': text}
                next_chunk = asyncio.create_task(anext(iterator))
    finally:
        next_chunk.cancel()
        next_status.cancel()
        await asyncio.gather(next_chunk, next_status, return_exceptions=True)
        await iterator.aclose()
