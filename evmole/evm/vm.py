from typing import Any

from .opcodes import Op, OpCode
from .stack import Stack
from .memory import Memory

E256 = 2**256
E256M1 = E256 - 1


class BadJumpDestError(Exception):
    pass


class BlacklistedOpError(Exception):
    pass


class UnsupportedOpError(Exception):
    pass


class Vm:
    def __init__(self, *, code: bytes, calldata, blacklisted_ops: set[OpCode] | None = None):
        self.code = code
        self.pc = 0
        self.stack = Stack()
        self.memory = Memory()
        self.stopped = False
        self.calldata = calldata
        self.blacklisted_ops = blacklisted_ops if blacklisted_ops is not None else set()

    def __str__(self):
        return '\n'.join(
            (
                f'Vm ({id(self):x}):',
                f' .pc = {hex(self.pc)} | {self.current_op()}',
                f' .stack = {self.stack}',
                f' .memory = {self.memory}',
            )
        )

    def __copy__(self):
        obj = Vm(code=self.code, calldata=self.calldata)
        obj.pc = self.pc
        obj.memory._seq = self.memory._seq
        obj.memory._data = self.memory._data[:]
        obj.stack._data = self.stack._data[:]
        obj.stopped = self.stopped
        obj.blacklisted_ops = self.blacklisted_ops
        return obj

    def current_op(self) -> OpCode:
        return OpCode(self.code[self.pc])

    def step(self) -> tuple[OpCode, int, *tuple[Any, ...]]:
        ret = self._exec_next_opcode()
        op, gas_used = ret[0], ret[1]
        assert gas_used != -1, f'Op {op} with unset gas_used'
        if op not in {Op.JUMP, Op.JUMPI}:
            self.pc += 1

        if self.pc >= len(self.code):
            self.stopped = True
        return ret

    def _exec_next_opcode(self) -> tuple[OpCode, int, *tuple[Any, ...]]:
        op = self.current_op()
        match op:
            case op if op in self.blacklisted_ops:
                raise BlacklistedOpError(op)

            case op if op >= Op.PUSH0 and op <= Op.PUSH32:
                n = op - Op.PUSH0
                args = self.code[(self.pc + 1) : (self.pc + 1 + n)].rjust(32, b'\x00')
                self.stack.push(args)
                self.pc += n
                return (op, 2 if n == 0 else 3)

            case op if op in {Op.JUMP, Op.JUMPI}:
                s0 = self.stack.pop_uint()
                if s0 >= len(self.code) or self.code[s0] != Op.JUMPDEST:
                    raise BadJumpDestError(f'pos {s0}')
                if op == Op.JUMPI:
                    s1 = self.stack.pop_uint()
                    if s1 == 0:
                        self.pc += 1
                        return (op, 10)
                self.pc = s0
                return (op, 8 if op == Op.JUMP else 10)

            case op if op >= Op.DUP1 and op <= Op.DUP16:
                self.stack.dup(op - Op.DUP1 + 1)
                return (op, 3)

            case Op.JUMPDEST:
                return (op, 1)

            case Op.REVERT:
                self.stack.pop()
                self.stack.pop()
                self.stopped = True
                return (op, 4)

            case op if op in {
                Op.EQ,
                Op.LT,
                Op.GT,
                Op.SUB,
                Op.ADD,
                Op.DIV,
                Op.MUL,
                Op.EXP,
                Op.XOR,
                Op.AND,
                Op.OR,
                Op.SHR,
                Op.SHL,
                Op.BYTE,
            }:
                raws0 = self.stack.pop()
                raws1 = self.stack.pop()

                s0 = int.from_bytes(raws0, 'big', signed=False)
                s1 = int.from_bytes(raws1, 'big', signed=False)

                gas_used = 3
                match op:
                    case Op.EQ:
                        res = 1 if s0 == s1 else 0
                    case Op.LT:
                        res = 1 if s0 < s1 else 0
                    case Op.GT:
                        res = 1 if s0 > s1 else 0
                    case Op.SUB:
                        res = (s0 - s1) & E256M1
                    case Op.ADD:
                        res = (s0 + s1) & E256M1
                    case Op.DIV:
                        res = 0 if s1 == 0 else s0 // s1
                        gas_used = 5
                    case Op.MUL:
                        res = (s0 * s1) & E256M1
                        gas_used = 5
                    case Op.EXP:
                        res = pow(s0, s1, E256)
                        gas_used = 50 * (1 + (s1.bit_length() // 8))  # ~approx
                    case Op.XOR:
                        res = s0 ^ s1
                    case Op.AND:
                        res = s0 & s1
                    case Op.OR:
                        res = s0 | s1
                    case Op.SHR:
                        res = 0 if s0 >= 256 else (s1 >> s0) & E256M1
                    case Op.SHL:
                        res = 0 if s0 >= 256 else (s1 << s0) & E256M1
                    case Op.BYTE:
                        res = 0 if s0 >= 32 else raws1[s0]
                    case _:
                        raise Exception(f'BUG: op {op} not handled in match')

                self.stack.push_uint(res)
                return (op, gas_used, raws0, raws1)

            case op if op in {Op.SLT, Op.SGT}:
                raws0 = self.stack.pop()
                raws1 = self.stack.pop()

                s0 = int.from_bytes(raws0, 'big', signed=True)
                s1 = int.from_bytes(raws1, 'big', signed=True)
                if op == Op.SLT:
                    res = 1 if s0 < s1 else 0
                else:
                    res = 1 if s0 > s1 else 0
                self.stack.push_uint(res)
                return (op, 3)

            case Op.ISZERO:
                raws0 = self.stack.pop()
                s0 = int.from_bytes(raws0, 'big', signed=False)
                res = 0 if s0 else 1
                self.stack.push_uint(res)
                return (op, 3, raws0)

            case Op.POP:
                self.stack.pop()
                return (op, 2)

            case Op.CALLVALUE:
                self.stack.push_uint(0)  # msg.value == 0
                return (op, 2)

            case Op.CALLDATALOAD:
                raws0 = self.stack.pop()
                offset = int.from_bytes(raws0, 'big', signed=False)
                self.stack.push(self.calldata.load(offset))
                return (op, 3, raws0)

            case Op.CALLDATASIZE:
                self.stack.push_uint(len(self.calldata))
                return (op, 2)

            case op if op >= Op.SWAP1 and op <= Op.SWAP16:
                self.stack.swap(op - Op.SWAP1 + 1)
                return (op, 3)

            case Op.MSTORE:
                offset = self.stack.pop_uint()
                value = self.stack.pop()
                self.memory.store(offset, value)
                return (op, 3)

            case Op.MLOAD:
                offset = self.stack.pop_uint()
                val, used = self.memory.load(offset)
                self.stack.push(val)
                return (op, 4, used)

            case Op.NOT:
                s0 = self.stack.pop_uint()
                self.stack.push_uint(E256M1 - s0)
                return (op, 3)

            case Op.SIGNEXTEND:
                s0 = self.stack.pop_uint()
                raws1 = self.stack.pop()
                s1 = int.from_bytes(raws1, 'big', signed=False)
                if s0 <= 31:
                    sign_bit = 1 << (s0 * 8 + 7)
                    if s1 & sign_bit:
                        res = s1 | (E256 - sign_bit)
                    else:
                        res = s1 & (sign_bit - 1)
                else:
                    res = s1

                self.stack.push_uint(res)
                return (op, 5, s0, raws1)

            case Op.ADDRESS:
                self.stack.push_uint(1)
                return (op, 2)

            case Op.CALLDATACOPY:
                mem_off = self.stack.pop_uint()
                src_off = self.stack.pop_uint()
                size = self.stack.pop_uint()
                value = self.calldata.load(src_off, size)
                self.memory.store(mem_off, value)
                return (op, 4)

            case _:
                raise UnsupportedOpError(op)
