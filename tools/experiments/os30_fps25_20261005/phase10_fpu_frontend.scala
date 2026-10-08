// Private geometry arithmetic feasibility probe. Reuses the VexiiRiscv
// floating-point adder; normal/zero inputs, RNE, retained reduced-FMA mode.
package vexiiriscv.test
import spinal.core._
import spinal.lib._
import spinal.lib.misc.pipeline._
import vexiiriscv.execute.fpu._

class GeometryFmaProbe extends Component {
  val io = new Bundle {
    val valid = in Bool()
    val a,b,c = in Bits(32 bits)
    val multiplyOnly = in Bool()
    val result = out Bits(32 bits)
    val resultValid = out Bool()
    val unsupported = out Bool()
  }
  val pip = new StagePipeline()
  val productParam=FloatUnpackedParam(255,-254,25)
  val operandParam=FloatUnpackedParam(127,-127,23)
  val A=Payload(FloatUnpacked(productParam))
  val C=Payload(FloatUnpacked(operandParam))
  val RD=Payload(Bool())
  val BAD=Payload(Bool())
  def unpack(bits:Bits):FloatUnpacked={
    val x=FloatUnpacked(operandParam)
    x.sign:=bits(31);x.quiet:=True
    x.exponent.raw := (bits(30 downto 23).asUInt.resize(9).asSInt-S(127,9 bits)).asBits.resized
    x.mantissa.raw:=bits(22 downto 0)
    x.mode:=FloatMode.NORMAL
    when(bits(30 downto 0)===0){x.mode:=FloatMode.ZERO}
    x
  }
  val s0=new pip.Area(0){
    valid:=io.valid
    val ua=unpack(io.a);val ub=unpack(io.b);val uc=unpack(io.c)
    val MUL=insert((U(1,1 bits)@@io.a(22 downto 0).asUInt)*(U(1,1 bits)@@io.b(22 downto 0).asUInt))
    val EXP=insert(ua.exponent+ub.exponent)
    val SIGN=insert(ua.sign^ub.sign)
    val ZERO=insert(ua.isZero||ub.isZero)
    val MONLY=insert(io.multiplyOnly)
    C:=uc
    when(io.multiplyOnly){C.setZero;C.sign:=False}
    BAD:=Seq(io.a,io.b,io.c).map(b=>(b(30 downto 23)===255)||((b(30 downto 23)===0)&&(b(22 downto 0)=/=0))).reduce(_||_)
    RD:=False
  }
  val s1=new pip.Area(1){
    val shift=s0.MUL.msb
    val normalized=shift ? s0.MUL(46 downto 0) | (s0.MUL(45 downto 0)@@False)
    A.sign:=s0.SIGN
    A.exponent := (s0.EXP+AFix(U(shift))).resized
    A.mantissa.raw := normalized(46 downto 22).asBits
    // Plain FMUL keeps a sticky bit. Reduced-accuracy retained FMA truncates
    // the product to 25 mantissa bits before the shared FpuAdd pipeline.
    when(s0.MONLY && normalized(21 downto 0).orR){ A.mantissa.raw(0):=True }
    A.mode:=FloatMode.NORMAL;A.quiet:=True
    when(s0.ZERO){A.setZero}
  }
  val add=new FpuAdd(A,C,RD,pip(2),pip(3),pip(4),pip(5),pip(6))
  val s7=new pip.Area(7){
    val value=FloatUnpacked(FloatUnpackedParam(1023,-1024,25))
    value.sign:=add.result.RESULT.sign
    value.exponent:=add.result.RESULT.exponent
    value.mantissa:=add.result.RESULT.mantissa.rounded(RoundType.SCRAP)
    value.mode:=add.result.RESULT.mode;value.quiet:=add.result.RESULT.quiet
    val man=value.mantissa.raw.asUInt
    val incr=man(1)&&(man(0)||man(2))
    val rounded=(man(24 downto 2)+^incr.asUInt)
    val exp=value.exponent.raw.asSInt.resize(12)+rounded.msb.asUInt.resize(12).asSInt
    val packexp=(exp+127).resize(8)
    io.result:=value.sign##packexp.asBits##rounded(22 downto 0).asBits
    when(value.isZero){io.result:=value.sign##B(0,31 bits)
      when(s0.MONLY){io.result:=s0.SIGN##B(0,31 bits)}
    }
    io.unsupported:=BAD || (value.isNormal && (exp < -126 || exp > 127)) || value.isNan || value.isInfinity
    io.resultValid:=isValid
    ready:=True
  }
  pip.build(withoutCollapse=true)
}
object GeometryFmaGenerate extends App {
  SpinalConfig(targetDirectory="fma-rtl").generateVerilog(new GeometryFmaProbe)
}
