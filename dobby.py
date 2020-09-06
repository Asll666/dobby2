if __name__ == '__main__':
    print("Please import this file from the interpreter")
    exit(-1)

from triton import *
import lief
import sys
import collections
import struct
import string
from enum import Enum

# ok so the plan
# instead of making our own cmdline interface, just use the python interpreter or ipython
# add our helper tools as helper functions in this library
# allow for iteration and reloading of this library as changes are added during runtime via importlib.reload
#
# must haves:
# - scriptable hooks
#       this is the real reason for moving away from a pure C++ codebase
#       While we had some limited scripting in the C++ code, here we can create functions with full access on the fly
# - save state to file
#       this is also more possible with python, as we can pickle created hooks
#       and easily serialize our saved sandbox changes
# - sandbox everything
#       the idea of dobby is to build the sandbox as the program runs, and be alerted to any side effects so we can build the env
# - sys file PE loading
# - (somewhat) quick emulation
#       it can't take more than a minute between sandbox prompts, otherwise it is unusable

#TODO current vague steps forward
#   0. test PE loading
#   1. emulation with callbacks
#   2. change tracking

#TODO other features
#   file symbol annotation
#   save/load state from file
#   per PE arch, instead of global hooks, annotations, etc

class Hook:
    def __init__(self, start, end, label="", handler=None):
        self.start = start
        self.end = end
        self.label = label
        self.handler = handler

    def __repr__(self):
        return f"Hook @ {hex(self.start)}:\"{self.label}\""

class Annotation:
    def __init__(self, start, end, mtype="UNK", label=""):
        self.start = start
        self.end = end
        self.mtype = mtype
        self.label = label

    def __repr__(self):
        return f"{hex(self.start)}-{hex(self.end)}=>\"{self.mtype}:{self.label}\""

class HookRet(Enum):
    CONT_INS = 0
    DONE_INS = 1
    STOP_INS = 2

class StepRet(Enum):
    ERR_STACK_OOB = -3
    ERR_IP_OOB = -2
    ERR = -1
    OK = 0
    HOOK_EXEC = 1
    HOOK_WRITE = 2
    HOOK_READ = 3
    HOOK_CB = 4
    PATH_FORKED = 5
    STACK_FORKED = 6
    BAD_INS = 7
    DREF_SYMBOLIC = 8
    DREF_OOB = 9
    

class Dobby:
    def __init__(self, apihookarea=0xffff414100000000):
        print("Starting Dobby 🤘")
        
        self.api = TritonContext(ARCH.X86_64)
        self.api.enableSymbolicEngine(True)

        self.lasthook = None
        self.lastins = None
        self.stepcb = None

        # setup hook stuff
        # hooks are for stopping execution, or running handlers
        self.hooks = [[],[],[]] # e, rw, w

        # setup annotation stuff
        # annotations are for noting things in memory that we track
        self.ann = []

        # setup bounds
        # bounds is for sandboxing areas we haven't setup yet
        self.bounds = []

        # save off types for checking later
        self.type_MemoryAccess = type(MemoryAccess(0,1))
        self.type_Register = type(self.api.registers.rax)

        # add annotation for the API_FUNC area
        self.apihooks = self.addAnn(apihookarea, apihookarea, "API_HOOKS", False, "API HOOKS")

    def printBounds(self):
        for b in self.bounds:
            print(hex(b[0]),'-',hex(b[1]))

    def printReg(self, reg, simp=True):
        print(reg, end=" = ")
        if self.api.isRegisterSymbolized(reg):
            s = self.api.getSymbolicRegister(reg)
            if s is not None:
                ast = s.getAst()
                if simp:
                    ast = self.api.simplify(ast, True)
                #TODO print ast with HEX and optional tabbing of args
                print(ast)
                return
        # concrete value
        print(hex(self.api.getConcreteRegisterValue(reg)))

    def printSymMem(self, addr, amt, stride, simp=True):
        if not self.inBounds(addr, amt):
            print("Warning, OOB memory")
        for i in range(0, amt, stride):
            memast = self.api.getMemoryAst(MemoryAccess(addr+i, stride))
            if simp:
                memast = self.api.simplify(memast, True)
            print(hex(addr+i)[2:].zfill(16), end=":  ")
            #TODO print ast with HEX and optional tabbing of args
            print(memast)

    def printMem(self, addr, amt, simp=True):
        if not self.inBounds(addr, amt):
            print("Warning, OOB memory")
        # read symbolic memory too
        hassym = False
        for i in range(0, amt):
            if self.api.isMemorySymbolized(MemoryAccess(addr+i, 1)):
                hassym = True
                break
        if hassym:
            print("Warning, contains symbolized memory")
            self.printSymMem(addr, amt, 8, simp)
            return
        mem = self.api.getConcreteMemoryAreaValue(addr, amt)
        hexdmp(mem, addr)

        
    def printRegMem(self, reg, amt, simp=True):
        # dref register, if not symbolic and call printMem
        if self.api.isRegisterSymbolized(reg):
            print("Symbolic Register")
            self.printReg(reg, simp)
        else:
            addr = self.api.getConcreteRegisterValue(reg)
            self.printMem(addr, amt, simp)

    def printStack(self, amt=0x60):
        self.printRegMem(self.api.registers.rsp, amt)

    def getu64(self, addr):
        return struct.unpack("Q", self.api.getConcreteMemoryAreaValue(addr, 8))[0]

    def setu64(self, addr, val):
        self.api.setConcreteMemoryAreaValue(addr, struct.pack("Q", val))

    def evalReg(self, reg):
        #TODO is there a way to only evaluate if we have set the syms this reg depends on?
        if self.api.isRegisterSymbolized(reg):
            val = self.api.getSymbolicRegisterValue(reg)
            self.api.setConcreteRegisterValue(reg, val)

    def evalMem(self, addr, size):
        #TODO is there a way to only evaluate if we have set the syms this area depends on?
        mem = b""
        for i in range(size):
            mem += self.api.getSymbolicMemoryValue(MemoryAccess(addr+i, 1))
            self.api.setConcreteMemoryValue(addr+i, mem)

    def loadPE(self, path, base):
        pe = lief.parse(path)

        dif = base - pe.optional_header.imagebase

        # load concrete mem vals from image
        # we need to load in header as well
        rawhdr = b""
        with open(path, "rb") as fp:
            rawhdr = fp.read(pe.sizeof_headers)
        self.api.setConcreteMemoryAreaValue(base, rawhdr)
        self.addAnn(base, base+len(rawhdr), "MAPPED_PE_HDR", True, pe.name)

        for phdr in pe.sections:
            start = base + phdr.virtual_address
            end = start + len(phdr.content)
            self.api.setConcreteMemoryAreaValue(base + phdr.virtual_address, phdr.content)

            if (end - start) < phdr.virtual_size:
                end = start + phdr.virtual_size

            # round end up to page size
            end = (end + 0xfff) & (~0xfff)
            
            #annotate the memory region
            self.addAnn(start, end, "MAPPED_PE", True, pe.name + '(' + phdr.name + ')')

        # do reloactions
        for r in pe.relocations:
             for re in pe.relocations:
                if re.type == lief.PE.RELOCATIONS_BASE_TYPES.DIR64:
                    a = re.address
                    val = self.getu64(base + a)

                    slid = val + dif

                    self.setu64(base + a, slid)
                else:
                    print(f"Warning: PE Loading: Unhandled relocation type {re.type}")

        # setup exception handlers
        #TODO

        # symbolize imports
        for i in pe.imports:
            for ie in i.entries:
                # extend the API HOOKS execution hook 
                hookaddr = self.apihooks.end
                self.apihooks.end += 8
                self.setu64(base + ie.iat_address, hookaddr)

                name = i.name + "::" + ie.name
                # create symbolic entry in the, if the address is used strangly
                # really this should be in the IAT, if the entry is a pointer to something bigger than 8 bytes
                #TODO
                # but for now, we just assume most of these are functions or pointers to something 8 or less bytes large
                self.api.symbolizeMemory(MemoryAccess(hookaddr, 8), "IAT val from " + pe.name + " for " + name)

                # create execution hook in hook are
                self.addHook(hookaddr, hookaddr+8, "e", None, False, "IAT entry from " + pe.name + " for " + name)

        self.updateBounds(self.apihooks.start, self.apihooks.end)
        
        # annotate symbols from image
        #TODO

        return pe

    def addHook(self, start, end, htype, handler=None, ub=False, label=""):
        # handler takes 3 args, (hook, addr, sz, op)
        # handler returns True to be a breakpoint, False to continue execution
        h = Hook(start, end, label, handler)
        added = False
        if 'e' in htype:
            added = True
            self.hooks[0].append(h)
        if 'r' in htype:
            added = True
            self.hooks[1].append(h)
        if 'w' in htype:
            added = True
            self.hooks[2].append(h)

        if not added:
            raise ValueError(f"Unknown Hook Type {htype}")
        elif ub:
            self.updateBounds(start, end)
        
        return h

    @staticmethod
    def rethook(hook, ctx, addr, sz, op):
        sp = ctx.api.getConcreteRegisterValue(ctx.api.registers.rsp)
        retaddr = ctx.getu64(sp)
        ctx.api.setConcreteRegisterValue(ctx.api.registers.rip, retaddr)
        ctx.api.setConcreteRegisterValue(ctx.api.registers.rax, 0)
        ctx.api.setConcreteRegisterValue(ctx.api.registers.rsp, sp+8)
        return HookRet.DONE_INS

    def updateBounds(self, start, end):
        insi = 0
        si = -1
        ei = -1
        combine = False

        if start > end:
            raise ValueError(f"Invalid bounds {start} -> {end}")

        # see if it is already in bounds, or starts/ends in a region
        for bi in range(len(self.bounds)):
            b = self.bounds[bi]
            if b[1] < start:
                insi = bi+1
            if b[0] <= start <= b[1]:
                si = bi
            if b[0] <= end <= b[1]:
                ei = bi

        if si == -1 and ei == -1:
            # add a new bounds area
            self.bounds.insert(insi, [start, end])
        elif si == ei:
            # we are good already
            pass
        elif si == -1:
            # extend the ei one
            self.bounds[ei][0] = start
            combine = True
        elif ei == -1:
            # extend the si one
            self.bounds[si][1] = end
            combine = True
        else:
            # combine two or more entries
            self.bounds[si][1] = self.bounds[ei][1]
            combine = True

        if combine:
            while insi+1 < len(self.bounds) and self.bounds[insi+1][1] <= d[insi][1]:
                del self.bounds[insi+1]

    def inBounds(self, addr, sz=1):
        #TODO binary search
        for b in self.bounds:
            if b[1] < (addr+sz):
                continue
            if b[0] > addr:
                break
            return True
        return False

    def addAnn(self, start, end, mtype, ub=False, label=""):
        if ub:
            self.updateBounds(start, end)

        ann = Annotation(start, end, mtype, label)
        self.ann.append(ann)
        return ann

    def initState(self, start, end, stackbase=0xffffb9872000000, priv=0):
        # zero or symbolize all registers
        for r in self.api.getAllRegisters():
            n = r.getName()
            sym = False
            if n.startswith("cr") or n in ["gs", "fs"]:
                sym = True

            if sym:
                self.api.symbolizeRegister(r, "Inital " + n)
            else:
                self.api.setConcreteRegisterValue(r, 0)
        # setup rflags to be sane
        self.api.setConcreteRegisterValue(
            self.api.registers.eflags,
            (1 << 9) | # interrupts enabled
            (priv << 12) | # IOPL
            (1 << 21) # support cpuid
        )

        # setup sane control registers instead of symbolizing them all?
        #TODO

        # create stack
        stackstart = stackbase - (0x1000 * 16)
        stackann = self.addAnn(stackstart, stackbase, "STACK", True, "Inital Stack")
        # add guard hook
        def stack_guard_hook(hk, ctx, addr, sz, op):
            # grow the stack, if we can
            nonlocal stackann

            newstart = stackann.start - 0x1000
            if ctx.inBounds(newstart, 0x1000):
                # error, stack ran into something else
                print(f"Stack overflow! Stack with top at {stackann.start} could not grow")
                return True

            # grow annotation
            stackann.start = newstart
            # grow bounds
            ctx.updateBounds(newstart, stackann[1])
            # move the hook
            hk.start = newstart - 0x1000
            hk.end = newstart
            return False

        self.addHook(stackstart - (0x1000), stackstart, "w", stack_guard_hook, False, "Stack Guard")

        # create end hook
        self.addHook(end, end+1, "e", None, False, "End Hit")

        # create heap
        #TODO

        # set initial rip and rsp
        self.api.setConcreteRegisterValue(self.api.registers.rip, start)
        self.api.setConcreteRegisterValue(self.api.registers.rsp, stackbase - 0x100)

        return True

    def getNextIns(self):
        # rip should never be symbolic when this function is called
        if self.api.isRegisterSymbolized(self.api.registers.rip):
            raise ValueError("Tried to get instruction with symbolized rip")
        rip = self.api.getConcreteRegisterValue(self.api.registers.rip)
        insbytes = self.api.getConcreteMemoryAreaValue(rip, 15)
        inst = Instruction(rip, insbytes)
        self.api.disassembly(inst)
        return inst

    def stepi(self, ins, ignorehook=False):
        if self.stepcb is not None:
            ret = self.stepcb(self)
            if ret == HookRet.STOP_INS:
                return StepRet.HOOK_CB
            if ret == HookRet.DONE_INS:
                return StepRet.OK

        # do pre-step stuff
        self.lasthook = None
        self.lastins = ins

        # rip and rsp should always be a concrete value at the beginning of this function
        rspreg = self.api.registers.rsp
        ripreg = self.api.registers.rip
        rsp = self.api.getConcreteRegisterValue(rspreg)
        rip = self.api.getConcreteRegisterValue(ripreg)

        if not self.inBounds(rip, ins.getSize()):
            return StepRet.ERR_IP_OOB

        if not self.inBounds(rsp, 8):
            return StepRet.ERR_STACK_OOB

        if not ignorehook:
            # check if rip is at a hooked execution location
            for eh in self.hooks[0]:
                if eh.start <= rip < eh.end:
                    # hooked
                    self.lasthook = eh

                    if eh.handler is not None:
                        hret = eh.handler(eh, self, rip, 1, "e")
                        if hret == HookRet.STOP_INS:
                            return StepRet.HOOK_EXEC
                        elif hret == HookRet.CONT_INS:
                            break
                        elif hret == HookRet.DONE_INS:
                            return StepRet.OK
                        else:
                            raise TypeError(f"Unknown return from hook handler for hook {eh}")
                    else:
                        return StepRet.HOOK_EXEC

        # check if we are about to do a memory deref of:
        #   a symbolic value
        #   a hooked location (if not ignorehook)
        #   an out of bounds location
        # we can't know beforehand if it is a write or not, so verify after the instruction
        #TODO how to automatically detect symbolic expressions that are evaluable based on variables we have set
        for o in ins.getOperands():
            #TODO check if non-register memory derefs are MemoryAccess as well
            if isinstance(o, self.type_MemoryAccess):
                lea = ins.getDisassembly().find("lea")
                nop = ins.getDisassembly().find("nop")
                if lea != -1 or nop != -1:
                    # get that fake crap out of here
                    continue
                # check base register isn't symbolic
                basereg = o.getBaseRegister()
                baseregid = basereg.getId()
                if baseregid != 0 and self.api.isRegisterSymbolized(basereg):
                    return StepRet.DREF_SYMBOLIC

                # check index register isn't symbolic
                indexreg = o.getIndexRegister()
                indexregid = indexreg.getId()
                if indexregid != 0 and self.api.isRegisterSymbolized(indexreg):
                    return StepRet.DREF_SYMBOLIC

                # check segment isn't symbolic
                segreg = o.getSegmentRegister()
                segregis = segreg.getId()
                if segreg.getId() != 0 and self.api.isRegisterSymbolized(segreg):
                    return StepRet.DREF_SYMBOLIC

                # calculate the address with displacement and scale
                addr = 0
                if baseregid != 0:
                    addr += self.api.getConcreteRegisterValue(basereg)

                if indexregid != 0:
                    scale = o.getScale().getValue()
                    addr += (scale * self.api.getConcreteRegisterValue(indexreg))

                disp = o.getDisplacement().getValue()
                addr += disp
                size = o.getSize()

                # check access is in bounds
                if not self.inBounds(addr, size):
                    return StepRet.DREF_OOB

                if not ignorehook:
                    # check if access is hooked
                    for rh in self.hooks[1]:
                        if rh.start <= addr < rh.end:
                            # hooked
                            self.lasthook = rh

                            if rh.handler is not None:
                                hret = rh.handler(eh, self, addr, size, "r")
                                if hret == HookRet.STOP_INS:
                                    return StepRet.HOOK_EXEC
                                elif hret == HookRet.CONT_INS:
                                    break
                                elif hret == HookRet.DONE_INS:
                                    return StepRet.OK
                                else:
                                    raise TypeError(f"Unknown return from hook handler for hook {rh}")
                            else:
                                return StepRet.HOOK_READ
                    #TODO check write hooks

        # actually do a step
        if not self.api.processing(ins):
            return StepRet.BAD_INS

        # check if we forked rip
        if self.api.isRegisterSymbolized(ripreg):
            return StepRet.PATH_FORKED
            # find what symbols it depends on
            # and use setConcreteVariableValue to give a concrete value for the var
            # then use getSymbolic*Value to evaluate the result

        # check if we forked rsp
        if self.api.isRegisterSymbolized(ripreg):
            return StepRet.STACK_FORKED

        # follow up on the write hooks
        if ins.isMemoryWrite():
            #TODO
            pass

        return StepRet.OK

    def step(self, printIns=True, ignorehook=True):
        ins = self.getNextIns()
        if printIns:
            #TODO if in API hooks, print API hook instead
            print(ins)
        return self.stepi(ins, ignorehook)

    def cont(self, printIns=True, ignoreFirst=True):
        if ignoreFirst:
            ret = self.step(printIns, True)
            if ret != StepRet.OK:
                return ret
        while True:
            ret = self.step(printIns, False)
            if ret != StepRet.OK:
                return ret

    def until(self, addr, printIns=True, ignoreFirst=True):
        #TODO
        pass

# util
def hexdmp(stuff, start=0):
    printable = string.digits + string.ascii_letters + string.punctuation + ' '
    rowlen = 0x10
    mid = (rowlen//2)-1
    for i in range(0, len(stuff), rowlen):
        # start of line
        print(hex(start + i)[2:].zfill(16), end=":  ")

        # bytes
        rowend = min(i+rowlen, len(stuff))
        for ci in range(i, rowend):
            print(stuff[ci:ci+1].hex(), end=(" " if ((ci & (rowlen-1)) != mid) else '-'))
            
        # padding
        empty = rowlen - (rowend - i)
        if empty != 0:
            # pad out
            print("   " * empty, end="")

        print(' ', end="")

        # ascii
        for c in stuff[i:rowend]:
            cs = chr(c)
            if cs in printable:
                print(cs, end="")
            else:
                print(".", end="")
        print()
