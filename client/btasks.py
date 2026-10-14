"""Optional Bitrix24 task-review service (a Telegram Mini App). Not part of the public version: configured() is
always False, so the watcher sends the plain "meeting ready" summary and never drafts tasks.
"""
DRAFT = "bitrix-draft.json"


def configured():
    return False


def load_draft(out):
    raise RuntimeError("the Bitrix24 task service is not included")


def push(out, summary_html=None, announce=True):
    raise RuntimeError("the Bitrix24 task service is not included")


def pull():
    return None
