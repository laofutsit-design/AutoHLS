"""One-shot PYNQ load tracing; importing this module never touches hardware."""
import json
import os
from pathlib import Path
import sys
import time


def trace_load(overlay, log_path, confirm_board=False):
    """Call the existing loader once, recording progress without replacing it.

    The caller must stop if completion is not confirmed. No retries, register
    reads, buffer allocation, or accelerator launch are performed here.
    """
    if confirm_board is not True:
        raise RuntimeError("Explicit board confirmation required")
    if sys.gettrace() is not None:
        raise RuntimeError("An existing debugger/trace must not be replaced")
    watched = {"download", "set_pl_clk", "_preload_binfile", "shutdown",
               "post_download", "reset"}
    # Never overwrite evidence from an earlier attempt.
    with Path(log_path).open("x", encoding="utf-8") as log:
        def record(event, **fields):
            fields.update(event=event, monotonic=time.monotonic())
            line = json.dumps(fields, sort_keys=True)
            log.write(line + "\n")
            log.flush()
            os.fsync(log.fileno())
            print(line, flush=True)
            # Let Jupyter's output thread transmit before the next risky call.
            time.sleep(0.1)

        def trace(frame, event, arg):
            module = frame.f_globals.get("__name__", "")
            name = frame.f_code.co_name
            if not module.startswith("pynq.") or name not in watched:
                return None
            if event in ("call", "return", "exception") or (
                    event == "line" and name == "download"):
                fields = {"function": module + "." + name,
                          "file": frame.f_code.co_filename, "line": frame.f_lineno}
                if event == "call" and name == "set_pl_clk":
                    fields["clock_args"] = {key: frame.f_locals.get(key)
                                            for key in ("clk_idx", "div0", "div1", "clk_mhz")}
                if event == "exception":
                    fields["error"] = arg[0].__name__ + ": " + str(arg[1])
                record(event, **fields)
            return trace

        record("load_begin")
        sys.settrace(trace)
        try:
            overlay.download()
        except BaseException as error:
            sys.settrace(None)
            record("load_failed", error=type(error).__name__ + ": " + str(error))
            raise
        finally:
            sys.settrace(None)
        record("load_complete")
