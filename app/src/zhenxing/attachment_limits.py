"""Selection UX limits, not a cross-request server batch quota.

The authenticated upload endpoint accepts one image per request. Each image
keeps its own consent, idempotency key, job, version, hash and explicit review.
Existing conversation evidence references retain their historical limit.
"""

MAX_IMAGES_PER_BATCH = 5
MAX_FILES_PER_UPLOAD_REQUEST = 1
