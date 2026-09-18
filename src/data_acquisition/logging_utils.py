import logging


class ContextAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        extra = kwargs.setdefault("extra", {})
        extra.setdefault("video_id", self.extra.get("video_id", "-"))
        extra.setdefault("channel_id", self.extra.get("channel_id", "-"))
        extra.setdefault("action", self.extra.get("action", "-"))
        return msg, kwargs


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(video_id)s %(channel_id)s %(action)s %(message)s",
                        datefmt="%Y-%m-%dT%H:%M:%SZ")
    logging.Formatter.converter = __import__("time").gmtime


def log_for(video_id: str = "-", channel_id: str = "-", action: str = "-") -> ContextAdapter:
    return ContextAdapter(logging.getLogger("data_acquisition"),
                          {"video_id": video_id, "channel_id": channel_id, "action": action})
