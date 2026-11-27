---
type: landing
owner: vgacap maintainers
reader: someone who wants an FPGA design to write a vgacap stream itself
review: 2027-05-27
---

# vgacap/rtl: capture in an FPGA

- [The raw framer](explanation-raw-framer.md): how samples become vgacap chunks inside the FPGA, and what happens when the sink falls behind.
- [How to add the raw framer's Verilog to an FPGA design](how-to-add-verilog.md): the generated Verilog in a Verilog design.
- [How to add the raw framer to a Migen or LiteX design](how-to-add-migen.md): the Migen module in a Migen or LiteX design.
- [The raw framer's ports, parameters and output](reference-raw-framer.md): ports, parameters, chunk layouts and throughput.
- [How to regenerate the raw framer's Verilog and run its tests](how-to-regenerate.md): after a change to the Migen source.
