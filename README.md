# Dobby2
---
#### What is Dobby?
Dobby is a hobby project I have created as a tool to help me reverse engineer a few highly obfuscated binaries.

Dobby is an emulator that provides interesting tools like a symbolic API, reverse taint analysis, multiple emulation engines, snapshots, and quick prototyping.

The tool is built upon a set of "providers" that work as backends. Currently the two backends include (Triton)[https://github.com/JonathanSalwan/Triton] and (Unicorn)[https://github.com/unicorn-engine/unicorn]. Having multiple different engines with different strengths that share an environment and snapshots is a large strength.

Other providers can be added simply by implementing the portions of an API that they can. (See interface.py)

## Features
- Kernel-mode Emulation
- x86_64 emulation
- Concolic Execution
- Reverse Taint Analysis
- Snapshotting
- Multiple Backends
- Execution Hooks
- Read/Write hooks
- Memory Annotations
- Instruction Trace Comparisons

## Q & A

#### How does this tool helps defeat strong obfuscation?
As long as a target binary being emulated does not know it is being emulated, many obfuscations can be ignored by researchers in favor of just noting side effects of the target binary. Simple emulation can often defeat many kinds of obfuscation.

However there is always the fear that the binary has detected it is being watched, and will act differently. Both differential execution and symbolic execution help Dobby detect these detections.

Differential execution in this case just means that Dobby can run the same code/memory/binary/hooks/etc with multiple different engines, and report to the researcher where two engines have different execution paths, or use memory differently. Combining this with reverse taint analysis allows us to narrow down a set of reasons why one engine acted different than the other.

Symbolic Execution (actually concolic execution in this case) allows us to denote areas of memory or registers as symbolic variables. Dobby will alert us when these variables are used as memory addresses, or used to decide which path execution will take. This allows us to denote much of our environment as symbolic and not worry about setting it up correctly until we see that the target binary even cares about it. It also allows us to do things like backward slicing to produce equations that tell us exactly how our variables were used to produce a value.

Many existing deobfuscation methods use some kind of lifting and optimization steps to try and produce equivalent code that is simpler. For a significantly complicated binary though, gathering the symbolic equations can explode in terms of memory and processing time. Dobby instead is not designed to be "fire and forget". Dobby works with the reverse engineer allowing symbolic expressions to be concretized into real values before the expressions grow too big for the machine to handle.

#### How does this tool compare to other projects?

This project is similar to a few other projects. For most use cases, you probably want a more mature tool. Below is a table comparing a few of them with Dobby.

 = | Dobby | Qiling | Speakeasy | PANDA | QEMU | Triton | Unicorn
---|-------|--------|-----------|-------|------|--------|---------
Usability | MediumLow - hobby project | High - Well designed API on Unicorn | High - Well designed API on Unicorn especially for Windows Kernel | Highish - Extension of QEmu | Medium - Not designed as a libary | Highish - Requires a bit of work for this use case (hence the creation of Dobby) | Very High - The Best API, but requires a bunch of work to set up environment (hence the creation of Qiling, Speakeasy, and Dobby)
Symbolic Support | Yes | No | No | Some with LLVR IR | No | Yes | No
Snapshots | Yes | Yes | No? | Yes | Yes | No | No
Multiple Backends | Yes | No | No | No | Yes | No | No
Actively Maintained | Still in hobby phase | Yes | Yes | Yes | Yes | Yes | Yes
Licence | ? | GPLv2 | MIT | GPLv2 | GPLv2 | Apache2 | GPLv2
Fun to use | Yes | Yes | Yes | Yesish | Kinda | Yes | Very Yes
Cool Name | No | Yes | Yes | No | Very No | Yes | Yes
Stems from QEMU | Yes | Yes | Yes | Yes | Is | No | Yes
Publications | No | Yes | Yes | Yes | Yes | Yes | Yes

(Qiling Framework)[https://github.com/qilingframework/qiling]

(Fireeye's Speakeasy)[https://github.com/fireeye/speakeasy]

(Panda)[https://github.com/panda-re/panda]

(QEMU (the OG))[https://www.qemu.org/]

(Triton)[https://github.com/JonathanSalwan/Triton]

(Unicorn)[https://github.com/unicorn-engine/unicorn]

## Example
See the file beepexample.py

## Instalation Notes
This project depends on the following python3 packages, which can be installed with pip:
- triton
- pyvex
- lief
- unicorn
