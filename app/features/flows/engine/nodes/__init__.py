"""Registre des handlers de nœuds : type → fonction handler."""

from .agent import execute_agent
from .approval import execute_approval
from .classification import execute_classification
from .determination import execute_determination
from .do_while import execute_do_while
from .edit import execute_edit
from .end import execute_end
from .for_loop import execute_for
from .http_node import execute_http
from .if_node import execute_if
from .merge import execute_merge
from .notification import execute_notification
from .start import execute_start
from .subflow import execute_subflow
from .switch import execute_switch
from .while_loop import execute_while

NODE_REGISTRY = {
    "start": execute_start,
    "end": execute_end,
    "if": execute_if,
    "switch": execute_switch,
    "merge": execute_merge,
    "edit": execute_edit,
    "for": execute_for,
    "while": execute_while,
    "do_while": execute_do_while,
    "http": execute_http,
    "notification": execute_notification,
    "approval": execute_approval,
    "flow": execute_subflow,
    "classification": execute_classification,
    "determination": execute_determination,
    "container": execute_agent,
    "agent": execute_agent,
}
