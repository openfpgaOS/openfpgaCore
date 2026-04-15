#include "Vtb_cram1_stress.h"
#include "verilated.h"
#include "verilated_vcd_c.h"

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    Verilated::traceEverOn(true);

    auto *top = new Vtb_cram1_stress;
    auto *tfp = new VerilatedVcdC;
    top->trace(tfp, 99);
    tfp->open("tb_cram1_stress.vcd");

    while (!Verilated::gotFinish()) {
        top->eval_step();
        top->eval_end_step();
        vluint64_t now = Verilated::time();
        tfp->dump(now);
        Verilated::timeInc(1);  // advance 1ns
    }

    tfp->close();
    delete tfp;
    delete top;
    return 0;
}
