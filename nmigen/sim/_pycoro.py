import inspect

from ..hdl import *
from ..hdl.ast import Statement
from ._cmds import *
from ._core import Process
from ._pyrtl import _ValueCompiler, _RHSValueCompiler, _StatementCompiler


__all__ = ["PyCoroProcess"]


class PyCoroProcess(Process):
    def __init__(self, state, domains, constructor, *, default_cmd=None):
        self.state = state
        self.domains = domains
        self.constructor = constructor
        self.default_cmd = default_cmd

        self.reset()

    def reset(self):
        self.runnable = True
        self.passive = False
        self.coroutine = self.constructor()
        self.exec_locals = {
            "slots": self.state.slots,
            "result": None,
            **_ValueCompiler.helpers
        }
        self.waits_on = set()

    def src_loc(self):
        coroutine = self.coroutine
        while coroutine.gi_yieldfrom is not None:
            coroutine = coroutine.gi_yieldfrom
        if inspect.isgenerator(coroutine):
            frame = coroutine.gi_frame
        if inspect.iscoroutine(coroutine):
            frame = coroutine.cr_frame
        return "{}:{}".format(inspect.getfile(frame), inspect.getlineno(frame))

    def run(self):
        if self.coroutine is None:
            return

        if self.waits_on:
            for signal in self.waits_on:
                self.state.remove_trigger(self, signal)
            self.waits_on.clear()

        response = None
        while True:
            try:
                command = self.coroutine.send(response)
                if command is None:
                    command = self.default_cmd
                response = None

                if isinstance(command, Value):
                    exec(_RHSValueCompiler.compile(self.state, command, mode="curr"),
                        self.exec_locals)
                    response = Const.normalize(self.exec_locals["result"], command.shape())

                elif isinstance(command, Statement):
                    exec(_StatementCompiler.compile(self.state, command),
                        self.exec_locals)

                elif type(command) is Tick:
                    domain = command.domain
                    if isinstance(domain, ClockDomain):
                        pass
                    elif domain in self.domains:
                        domain = self.domains[domain]
                    else:
                        raise NameError("Received command {!r} that refers to a nonexistent "
                                        "domain {!r} from process {!r}"
                                        .format(command, command.domain, self.src_loc()))
                    self.state.add_trigger(self, domain.clk,
                                           trigger=1 if domain.clk_edge == "pos" else 0)
                    if domain.rst is not None and domain.async_reset:
                        self.state.add_trigger(self, domain.rst, trigger=1)
                    return

                elif type(command) is Settle:
                    self.state.timeline.delay(None, self)
                    return

                elif type(command) is Delay:
                    self.state.timeline.delay(command.interval, self)
                    return

                elif type(command) is Passive:
                    self.passive = True

                elif type(command) is Active:
                    self.passive = False

                elif command is None: # only possible if self.default_cmd is None
                    raise TypeError("Received default command from process {!r} that was added "
                                    "with add_process(); did you mean to add this process with "
                                    "add_sync_process() instead?"
                                    .format(self.src_loc()))

                else:
                    raise TypeError("Received unsupported command {!r} from process {!r}"
                                    .format(command, self.src_loc()))

            except StopIteration:
                self.passive = True
                self.coroutine = None
                return

            except Exception as exn:
                self.coroutine.throw(exn)
