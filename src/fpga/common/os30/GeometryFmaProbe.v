// Generator : SpinalHDL dev    git head : 6f8510cdbb8ad7b8bcc0f6d58395669c4c4d7e2d
// Component : GeometryFmaProbe
// Git hash  : 81842b296999de1087b814227bd5296bac0bf361

`timescale 1ns/1ps

module GeometryFmaProbe (
  input  wire          io_valid,
  input  wire [31:0]   io_a,
  input  wire [31:0]   io_b,
  input  wire [31:0]   io_c,
  input  wire          io_multiplyOnly,
  output reg  [31:0]   io_result,
  output wire          io_resultValid,
  output wire          io_unsupported,
  input  wire          clk,
  input  wire          reset
);
  localparam FloatMode_ZERO = 2'd0;
  localparam FloatMode_INF = 2'd1;
  localparam FloatMode_NAN = 2'd2;
  localparam FloatMode_NORMAL = 2'd3;

  wire       [8:0]    _zz_s0_ua_exponent;
  wire       [8:0]    _zz_s0_ua_exponent_1;
  wire       [8:0]    _zz_s0_ua_exponent_2;
  wire       [8:0]    _zz_s0_ua_exponent_3;
  wire       [7:0]    _zz_s0_ua_exponent_4;
  wire       [8:0]    _zz_s0_ub_exponent;
  wire       [8:0]    _zz_s0_ub_exponent_1;
  wire       [8:0]    _zz_s0_ub_exponent_2;
  wire       [8:0]    _zz_s0_ub_exponent_3;
  wire       [7:0]    _zz_s0_ub_exponent_4;
  wire       [8:0]    _zz_s0_uc_exponent;
  wire       [8:0]    _zz_s0_uc_exponent_1;
  wire       [8:0]    _zz_s0_uc_exponent_2;
  wire       [8:0]    _zz_s0_uc_exponent_3;
  wire       [7:0]    _zz_s0_uc_exponent_4;
  wire       [8:0]    _zz_pip_node_0_s0_EXP;
  wire       [8:0]    _zz_pip_node_0_s0_EXP_1;
  wire       [8:0]    _zz_pip_node_0_s0_EXP_2;
  wire       [7:0]    _zz_pip_node_0_s0_EXP_3;
  wire       [8:0]    _zz_pip_node_0_s0_EXP_4;
  wire       [7:0]    _zz_pip_node_0_s0_EXP_5;
  wire       [7:0]    _zz_pip_node_0_C_exponent;
  wire       [7:0]    _zz_pip_node_0_BAD;
  wire       [7:0]    _zz_pip_node_0_BAD_1;
  wire       [22:0]   _zz_pip_node_0_BAD_2;
  wire       [22:0]   _zz_pip_node_0_BAD_3;
  wire       [7:0]    _zz_pip_node_0_BAD_4;
  wire       [7:0]    _zz_pip_node_0_BAD_5;
  wire       [22:0]   _zz_pip_node_0_BAD_6;
  wire       [22:0]   _zz_pip_node_0_BAD_7;
  wire       [8:0]    _zz_pip_node_1_A_exponent;
  wire       [8:0]    _zz_pip_node_1_A_exponent_1;
  wire       [8:0]    _zz_pip_node_1_A_exponent_2;
  wire       [8:0]    _zz_pip_node_1_A_exponent_3;
  wire       [8:0]    _zz_pip_node_1_A_exponent_4;
  wire       [1:0]    _zz_pip_node_1_A_exponent_5;
  wire       [9:0]    _zz_pip_node_2_add_preShift_exp21;
  wire       [9:0]    _zz_pip_node_2_add_preShift_exp21_1;
  wire       [9:0]    _zz_pip_node_2_add_preShift_exp21_2;
  wire       [7:0]    _zz_pip_node_2_add_preShift_exp21_3;
  wire       [9:0]    _zz_pip_node_2_add_preShift_exp21_4;
  wire       [8:0]    _zz_pip_node_2_add_preShift_exp21_5;
  wire       [9:0]    _zz_pip_node_2_add_preShift_exp12;
  wire       [9:0]    _zz_pip_node_2_add_preShift_exp12_1;
  wire       [9:0]    _zz_pip_node_2_add_preShift_exp12_2;
  wire       [8:0]    _zz_pip_node_2_add_preShift_exp12_3;
  wire       [9:0]    _zz_pip_node_2_add_preShift_exp12_4;
  wire       [7:0]    _zz_pip_node_2_add_preShift_exp12_5;
  wire       [9:0]    _zz__zz_when_AFix_l1203;
  wire       [9:0]    _zz__zz_when_AFix_l1203_1;
  wire       [8:0]    _zz_pip_node_2_add_preShift_rs1ExponentEqual;
  wire       [8:0]    _zz_pip_node_2_add_preShift_rs1ExponentEqual_1;
  wire       [7:0]    _zz_pip_node_2_add_preShift_rs1ExponentEqual_2;
  wire       [24:0]   _zz_pip_node_2_add_preShift_rs1MantissaBigger;
  wire       [25:0]   _zz_pip_node_3_add_shifter_xMantissa;
  wire       [25:0]   _zz_pip_node_3_add_shifter_xMantissa_1;
  wire       [24:0]   _zz_pip_node_3_add_shifter_xMantissa_2;
  wire       [24:0]   _zz_pip_node_3_add_shifter_xMantissa_3;
  wire       [25:0]   _zz_pip_node_3_add_shifter_xMantissa_4;
  wire       [25:0]   _zz_pip_node_3_add_shifter_yMantissaUnshifted;
  wire       [25:0]   _zz_pip_node_3_add_shifter_yMantissaUnshifted_1;
  wire       [24:0]   _zz_pip_node_3_add_shifter_yMantissaUnshifted_2;
  wire       [24:0]   _zz_pip_node_3_add_shifter_yMantissaUnshifted_3;
  wire       [25:0]   _zz_pip_node_3_add_shifter_yMantissaUnshifted_4;
  wire       [27:0]   _zz__zz_when_Utils_l1736_3;
  wire       [27:0]   _zz__zz_when_Utils_l1736_2;
  wire       [27:0]   _zz__zz_when_Utils_l1736_1;
  wire       [27:0]   _zz__zz_when_Utils_l1736;
  wire       [27:0]   _zz__zz_pip_node_3_add_shifter_shifter;
  wire       [27:0]   _zz_pip_node_3_add_shifter_shifter_2;
  wire       [0:0]    _zz_pip_node_3_add_shifter_shifter_3;
  wire       [8:0]    _zz_pip_node_3_add_shifter_xyExponent;
  wire       [8:0]    _zz_pip_node_3_add_shifter_xyExponent_1;
  wire       [7:0]    _zz_pip_node_3_add_shifter_xyExponent_2;
  wire       [27:0]   _zz_pip_node_4_add_math_ySigned_1;
  wire       [27:0]   _zz_pip_node_4_add_math_ySigned_2;
  wire       [27:0]   _zz_pip_node_4_add_math_ySigned_3;
  wire       [27:0]   _zz_pip_node_4_add_math_ySigned_4;
  wire       [0:0]    _zz_pip_node_4_add_math_ySigned_5;
  wire       [28:0]   _zz_pip_node_4_add_math_xyMantissa;
  wire       [28:0]   _zz_pip_node_4_add_math_xyMantissa_1;
  wire       [28:0]   _zz_pip_node_4_add_math_xyMantissa_2;
  wire       [27:0]   _zz_pip_node_4_add_math_xyMantissa_3;
  wire       [26:0]   _zz_pip_node_4_add_math_xyMantissa_4;
  wire       [28:0]   _zz_pip_node_4_add_math_xyMantissa_5;
  wire       [27:0]   _zz_pip_node_4_add_math_xyMantissa_6;
  wire                _zz__zz_pip_node_5_add_norm_shiftOh;
  wire       [0:0]    _zz__zz_pip_node_5_add_norm_shiftOh_1;
  wire       [18:0]   _zz__zz_pip_node_5_add_norm_shiftOh_2;
  wire                _zz__zz_pip_node_5_add_norm_shiftOh_3;
  wire       [0:0]    _zz__zz_pip_node_5_add_norm_shiftOh_4;
  wire       [7:0]    _zz__zz_pip_node_5_add_norm_shiftOh_5;
  wire       [9:0]    _zz_pip_node_6_add_result_exponent;
  wire       [9:0]    _zz_pip_node_6_add_result_exponent_1;
  wire       [9:0]    _zz_pip_node_6_add_result_exponent_2;
  wire       [9:0]    _zz_pip_node_6_add_result_exponent_3;
  wire       [9:0]    _zz_pip_node_6_add_result_exponent_4;
  wire       [8:0]    _zz_pip_node_6_add_result_exponent_5;
  wire       [9:0]    _zz_pip_node_6_add_result_exponent_6;
  wire       [5:0]    _zz_pip_node_6_add_result_exponent_7;
  wire       [9:0]    _zz_pip_node_6_add_result_exponent_8;
  wire       [1:0]    _zz_pip_node_6_add_result_exponent_9;
  wire       [28:0]   _zz_pip_node_6_add_result_RESULT_mantissa;
  wire       [9:0]    _zz_pip_node_6_add_result_RESULT_exponent;
  wire       [9:0]    _zz_pip_node_6_add_result_RESULT_exponent_1;
  wire       [10:0]   _zz_s7_value_exponent;
  wire       [9:0]    _zz_s7_value_exponent_1;
  wire       [23:0]   _zz_s7_rounded;
  wire       [1:0]    _zz_s7_rounded_1;
  wire       [11:0]   _zz_s7_exp;
  wire       [10:0]   _zz_s7_exp_1;
  wire       [11:0]   _zz_s7_exp_2;
  wire       [11:0]   _zz_s7_exp_3;
  wire       [0:0]    _zz_s7_exp_4;
  wire       [11:0]   _zz_s7_packexp;
  wire       [8:0]    _zz_pip_node_1_s0_EXP;
  wire       [7:0]    _zz_pip_node_1_C_exponent;
  wire       [7:0]    _zz_pip_node_2_C_exponent;
  wire       [8:0]    _zz_pip_node_2_A_exponent;
  wire       [7:0]    _zz_pip_node_3_C_exponent;
  wire       [8:0]    _zz_pip_node_3_A_exponent;
  wire       [7:0]    _zz_pip_node_4_C_exponent;
  wire       [8:0]    _zz_pip_node_4_A_exponent;
  wire       [8:0]    _zz_pip_node_4_add_shifter_xyExponent;
  wire       [7:0]    _zz_pip_node_5_C_exponent;
  wire       [8:0]    _zz_pip_node_5_A_exponent;
  wire       [8:0]    _zz_pip_node_5_add_shifter_xyExponent;
  wire       [7:0]    _zz_pip_node_6_C_exponent;
  wire       [8:0]    _zz_pip_node_6_A_exponent;
  wire       [8:0]    _zz_pip_node_6_add_shifter_xyExponent;
  wire       [9:0]    _zz_pip_node_7_add_result_RESULT_exponent;
  wire                pip_node_6_isValid;
  wire                pip_node_6_isReady;
  wire                pip_node_5_isValid;
  wire                pip_node_5_isReady;
  reg                 pip_node_6_BAD;
  reg                 pip_node_6_s0_MONLY;
  reg                 pip_node_6_s0_SIGN;
  wire                pip_node_4_isValid;
  wire                pip_node_4_isReady;
  reg                 pip_node_5_add_math_roundingScrap;
  reg        [8:0]    pip_node_5_add_shifter_xyExponent;
  reg                 pip_node_5_add_shifter_xySign;
  reg                 pip_node_5_RD;
  reg                 pip_node_5_BAD;
  reg                 pip_node_5_s0_MONLY;
  reg                 pip_node_5_s0_SIGN;
  wire                pip_node_3_isValid;
  wire                pip_node_3_isReady;
  reg        [8:0]    pip_node_4_add_shifter_xyExponent;
  reg                 pip_node_4_add_shifter_xySign;
  reg                 pip_node_4_RD;
  reg                 pip_node_4_BAD;
  reg                 pip_node_4_s0_MONLY;
  reg                 pip_node_4_s0_SIGN;
  wire                pip_node_2_isValid;
  wire                pip_node_2_isReady;
  reg                 pip_node_3_add_preShift_passThrough;
  reg                 pip_node_3_add_preShift_needSub;
  reg                 pip_node_3_RD;
  reg                 pip_node_3_BAD;
  reg                 pip_node_3_s0_MONLY;
  reg                 pip_node_3_s0_SIGN;
  wire                pip_node_1_isValid;
  wire                pip_node_1_isReady;
  reg                 pip_node_2_RD;
  reg                 pip_node_2_BAD;
  reg                 pip_node_2_s0_MONLY;
  reg                 pip_node_2_s0_SIGN;
  wire                pip_node_0_isValid;
  wire                pip_node_0_isReady;
  reg                 pip_node_1_RD;
  reg                 pip_node_1_BAD;
  reg        [1:0]    pip_node_1_C_mode;
  reg                 pip_node_1_C_quiet;
  reg                 pip_node_1_C_sign;
  reg        [7:0]    pip_node_1_C_exponent;
  reg        [22:0]   pip_node_1_C_mantissa;
  reg                 pip_node_7_valid;
  reg                 pip_node_6_valid;
  reg                 pip_node_5_valid;
  reg                 pip_node_4_valid;
  reg                 pip_node_3_valid;
  reg                 pip_node_2_valid;
  reg                 pip_node_1_valid;
  wire                pip_node_0_ready;
  wire                pip_node_1_ready;
  wire                pip_node_2_ready;
  wire                pip_node_3_ready;
  wire                pip_node_4_ready;
  wire                pip_node_5_ready;
  wire                pip_node_6_ready;
  wire                pip_node_7_ready;
  wire                pip_node_7_isValid;
  reg                 pip_node_7_BAD;
  reg                 pip_node_7_s0_SIGN;
  reg                 pip_node_7_s0_MONLY;
  reg        [1:0]    pip_node_7_add_result_RESULT_mode;
  reg                 pip_node_7_add_result_RESULT_quiet;
  reg                 pip_node_7_add_result_RESULT_sign;
  reg        [9:0]    pip_node_7_add_result_RESULT_exponent;
  reg        [27:0]   pip_node_7_add_result_RESULT_mantissa;
  reg                 pip_node_6_RD;
  reg                 pip_node_6_add_norm_xyMantissaZero;
  reg                 pip_node_6_add_norm_forceZero;
  reg                 pip_node_6_add_norm_forceInfinity;
  reg                 pip_node_6_add_norm_forceNan;
  wire                pip_node_6_add_result_NV;
  reg        [1:0]    pip_node_6_C_mode;
  reg                 pip_node_6_C_quiet;
  reg                 pip_node_6_C_sign;
  reg        [7:0]    pip_node_6_C_exponent;
  reg        [22:0]   pip_node_6_C_mantissa;
  reg        [1:0]    pip_node_6_A_mode;
  reg                 pip_node_6_A_quiet;
  reg                 pip_node_6_A_sign;
  reg        [8:0]    pip_node_6_A_exponent;
  reg        [24:0]   pip_node_6_A_mantissa;
  reg                 pip_node_6_add_norm_infinityNan;
  reg                 pip_node_6_add_math_roundingScrap;
  reg                 pip_node_6_add_shifter_xySign;
  reg        [1:0]    pip_node_6_add_result_RESULT_mode;
  reg                 pip_node_6_add_result_RESULT_quiet;
  reg                 pip_node_6_add_result_RESULT_sign;
  wire       [9:0]    pip_node_6_add_result_RESULT_exponent;
  wire       [27:0]   pip_node_6_add_result_RESULT_mantissa;
  wire       [27:0]   pip_node_6_add_result_mantissa;
  reg        [27:0]   pip_node_6_add_math_xyMantissa;
  wire       [9:0]    pip_node_6_add_result_exponent;
  reg        [4:0]    pip_node_6_add_norm_shift;
  reg        [8:0]    pip_node_6_add_shifter_xyExponent;
  wire                pip_node_5_add_norm_xyMantissaZero;
  wire                pip_node_5_add_norm_forceNan;
  wire                pip_node_5_add_norm_infinityNan;
  wire                pip_node_5_add_norm_forceZero;
  wire                pip_node_5_add_norm_forceInfinity;
  reg        [1:0]    pip_node_5_C_mode;
  reg                 pip_node_5_C_quiet;
  reg                 pip_node_5_C_sign;
  reg        [7:0]    pip_node_5_C_exponent;
  reg        [22:0]   pip_node_5_C_mantissa;
  reg        [1:0]    pip_node_5_A_mode;
  reg                 pip_node_5_A_quiet;
  reg                 pip_node_5_A_sign;
  reg        [8:0]    pip_node_5_A_exponent;
  reg        [24:0]   pip_node_5_A_mantissa;
  wire       [4:0]    pip_node_5_add_norm_shift;
  wire       [27:0]   pip_node_5_add_norm_shiftOh;
  reg        [27:0]   pip_node_5_add_math_xyMantissa;
  wire       [27:0]   pip_node_4_add_math_xyMantissa;
  reg        [25:0]   pip_node_4_add_shifter_xMantissa;
  wire       [27:0]   pip_node_4_add_math_ySigned;
  reg                 pip_node_4_add_preShift_needSub;
  reg        [26:0]   pip_node_4_add_shifter_yMantissa;
  reg        [1:0]    pip_node_4_C_mode;
  reg                 pip_node_4_C_quiet;
  reg                 pip_node_4_C_sign;
  reg        [7:0]    pip_node_4_C_exponent;
  reg        [22:0]   pip_node_4_C_mantissa;
  reg        [1:0]    pip_node_4_A_mode;
  reg                 pip_node_4_A_quiet;
  reg                 pip_node_4_A_sign;
  reg        [8:0]    pip_node_4_A_exponent;
  reg        [24:0]   pip_node_4_A_mantissa;
  reg                 pip_node_4_add_math_roundingScrap;
  reg                 pip_node_4_add_preShift_passThrough;
  reg        [27:0]   pip_node_4_add_shifter_shifter;
  wire       [8:0]    pip_node_3_add_shifter_xyExponent;
  wire       [26:0]   pip_node_3_add_shifter_yMantissa;
  wire       [27:0]   pip_node_3_add_shifter_shifter;
  reg        [27:0]   _zz_pip_node_3_add_shifter_shifter;
  reg        [27:0]   _zz_when_Utils_l1736;
  reg        [27:0]   _zz_when_Utils_l1736_1;
  reg        [27:0]   _zz_when_Utils_l1736_2;
  reg        [27:0]   _zz_when_Utils_l1736_3;
  reg        [4:0]    pip_node_3_add_preShift_expDifAbsSat;
  wire       [25:0]   pip_node_3_add_shifter_yMantissaUnshifted;
  wire       [25:0]   pip_node_3_add_shifter_xMantissa;
  wire                pip_node_3_add_shifter_xySign;
  reg        [1:0]    pip_node_3_C_mode;
  reg                 pip_node_3_C_quiet;
  reg                 pip_node_3_C_sign;
  reg        [7:0]    pip_node_3_C_exponent;
  reg        [22:0]   pip_node_3_C_mantissa;
  reg        [1:0]    pip_node_3_A_mode;
  reg                 pip_node_3_A_quiet;
  reg                 pip_node_3_A_sign;
  reg        [8:0]    pip_node_3_A_exponent;
  reg        [24:0]   pip_node_3_A_mantissa;
  reg                 pip_node_3_add_preShift_absRs1Bigger;
  wire       [4:0]    pip_node_2_add_preShift_expDifAbsSat;
  wire                pip_node_2_add_preShift_passThrough;
  wire                pip_node_2_add_preShift_needSub;
  wire                pip_node_2_add_preShift_absRs1Bigger;
  wire                pip_node_2_add_preShift_rs1MantissaBigger;
  wire                pip_node_2_add_preShift_rs1ExponentEqual;
  wire                pip_node_2_add_preShift_rs1ExponentBigger;
  wire       [8:0]    pip_node_2_add_preShift_expDifAbs;
  wire       [9:0]    pip_node_2_add_preShift_exp12;
  wire       [9:0]    pip_node_2_add_preShift_exp21;
  reg        [1:0]    pip_node_2_A_mode;
  reg                 pip_node_2_A_quiet;
  reg                 pip_node_2_A_sign;
  reg        [8:0]    pip_node_2_A_exponent;
  reg        [24:0]   pip_node_2_A_mantissa;
  reg        [1:0]    pip_node_2_C_mode;
  reg                 pip_node_2_C_quiet;
  reg                 pip_node_2_C_sign;
  reg        [7:0]    pip_node_2_C_exponent;
  reg        [22:0]   pip_node_2_C_mantissa;
  reg                 pip_node_1_s0_ZERO;
  reg                 pip_node_1_s0_MONLY;
  reg        [8:0]    pip_node_1_s0_EXP;
  reg                 pip_node_1_s0_SIGN;
  reg        [1:0]    pip_node_1_A_mode;
  wire                pip_node_1_A_quiet;
  wire                pip_node_1_A_sign;
  wire       [8:0]    pip_node_1_A_exponent;
  reg        [24:0]   pip_node_1_A_mantissa;
  reg        [47:0]   pip_node_1_s0_MUL;
  wire                pip_node_0_RD;
  wire                pip_node_0_BAD;
  reg        [1:0]    pip_node_0_C_mode;
  wire                pip_node_0_C_quiet;
  reg                 pip_node_0_C_sign;
  wire       [7:0]    pip_node_0_C_exponent;
  wire       [22:0]   pip_node_0_C_mantissa;
  wire                pip_node_0_s0_MONLY;
  wire                pip_node_0_s0_ZERO;
  wire                pip_node_0_s0_SIGN;
  wire       [8:0]    pip_node_0_s0_EXP;
  wire       [47:0]   pip_node_0_s0_MUL;
  wire                pip_node_0_valid;
  reg        [1:0]    s0_ua_mode;
  wire                s0_ua_quiet;
  wire                s0_ua_sign;
  wire       [7:0]    s0_ua_exponent;
  wire       [22:0]   s0_ua_mantissa;
  wire                when_GeometryFmaProbe_l31;
  reg        [1:0]    s0_ub_mode;
  wire                s0_ub_quiet;
  wire                s0_ub_sign;
  wire       [7:0]    s0_ub_exponent;
  wire       [22:0]   s0_ub_mantissa;
  wire                when_GeometryFmaProbe_l31_1;
  reg        [1:0]    s0_uc_mode;
  wire                s0_uc_quiet;
  wire                s0_uc_sign;
  wire       [7:0]    s0_uc_exponent;
  wire       [22:0]   s0_uc_mantissa;
  wire                when_GeometryFmaProbe_l31_2;
  wire                s1_shift;
  wire       [46:0]   s1_normalized;
  wire                when_GeometryFmaProbe_l55;
  wire       [9:0]    _zz_when_AFix_l1203;
  reg        [9:0]    _zz_pip_node_2_add_preShift_expDifAbs;
  wire                when_AFix_l1203;
  reg        [4:0]    _zz_pip_node_2_add_preShift_expDifAbsSat;
  wire                when_UInt_l119;
  wire       [27:0]   _zz_when_Utils_l1736_4;
  reg                 _zz_pip_node_3_add_shifter_shifter_1;
  wire                when_Utils_l1736;
  wire                when_Utils_l1736_1;
  wire                when_Utils_l1736_2;
  wire                when_Utils_l1736_3;
  wire                when_Utils_l1736_4;
  wire                when_FpuAdd_l56;
  wire       [26:0]   _zz_pip_node_4_add_math_ySigned;
  wire       [27:0]   _zz_pip_node_5_add_norm_shiftOh;
  wire                _zz_pip_node_5_add_norm_shiftOh_1;
  wire                _zz_pip_node_5_add_norm_shiftOh_2;
  wire                _zz_pip_node_5_add_norm_shiftOh_3;
  wire                _zz_pip_node_5_add_norm_shiftOh_4;
  wire                _zz_pip_node_5_add_norm_shiftOh_5;
  wire                _zz_pip_node_5_add_norm_shiftOh_6;
  wire                _zz_pip_node_5_add_norm_shiftOh_7;
  wire                _zz_pip_node_5_add_norm_shiftOh_8;
  wire                _zz_pip_node_5_add_norm_shiftOh_9;
  wire                _zz_pip_node_5_add_norm_shiftOh_10;
  wire                _zz_pip_node_5_add_norm_shiftOh_11;
  wire                _zz_pip_node_5_add_norm_shiftOh_12;
  wire                _zz_pip_node_5_add_norm_shiftOh_13;
  wire                _zz_pip_node_5_add_norm_shiftOh_14;
  wire                _zz_pip_node_5_add_norm_shiftOh_15;
  wire                _zz_pip_node_5_add_norm_shiftOh_16;
  wire                _zz_pip_node_5_add_norm_shiftOh_17;
  wire                _zz_pip_node_5_add_norm_shiftOh_18;
  wire                _zz_pip_node_5_add_norm_shiftOh_19;
  wire                _zz_pip_node_5_add_norm_shiftOh_20;
  wire                _zz_pip_node_5_add_norm_shiftOh_21;
  wire                _zz_pip_node_5_add_norm_shiftOh_22;
  wire                _zz_pip_node_5_add_norm_shiftOh_23;
  wire                _zz_pip_node_5_add_norm_shiftOh_24;
  wire                _zz_pip_node_5_add_norm_shiftOh_25;
  wire                _zz_pip_node_5_add_norm_shiftOh_26;
  wire                _zz_pip_node_5_add_norm_shiftOh_27;
  reg        [27:0]   _zz_pip_node_5_add_norm_shiftOh_28;
  wire                _zz_pip_node_5_add_norm_shiftOh_29;
  wire                _zz_pip_node_5_add_norm_shiftOh_30;
  wire                _zz_pip_node_5_add_norm_shiftOh_31;
  wire                _zz_pip_node_5_add_norm_shiftOh_32;
  wire                _zz_pip_node_5_add_norm_shiftOh_33;
  wire                _zz_pip_node_5_add_norm_shiftOh_34;
  wire                _zz_pip_node_5_add_norm_shiftOh_35;
  wire                _zz_pip_node_5_add_norm_shiftOh_36;
  wire                _zz_pip_node_5_add_norm_shiftOh_37;
  wire                _zz_pip_node_5_add_norm_shiftOh_38;
  wire                _zz_pip_node_5_add_norm_shift;
  wire                _zz_pip_node_5_add_norm_shift_1;
  wire                _zz_pip_node_5_add_norm_shift_2;
  wire                _zz_pip_node_5_add_norm_shift_3;
  wire                _zz_pip_node_5_add_norm_shift_4;
  wire                _zz_pip_node_5_add_norm_shift_5;
  wire                _zz_pip_node_5_add_norm_shift_6;
  wire                _zz_pip_node_5_add_norm_shift_7;
  wire                _zz_pip_node_5_add_norm_shift_8;
  wire                _zz_pip_node_5_add_norm_shift_9;
  wire                _zz_pip_node_5_add_norm_shift_10;
  wire                _zz_pip_node_5_add_norm_shift_11;
  wire                _zz_pip_node_5_add_norm_shift_12;
  wire                _zz_pip_node_5_add_norm_shift_13;
  wire                _zz_pip_node_5_add_norm_shift_14;
  wire                _zz_pip_node_5_add_norm_shift_15;
  wire                _zz_pip_node_5_add_norm_shift_16;
  wire                _zz_pip_node_5_add_norm_shift_17;
  wire                _zz_pip_node_5_add_norm_shift_18;
  wire                _zz_pip_node_5_add_norm_shift_19;
  wire                _zz_pip_node_5_add_norm_shift_20;
  wire                _zz_pip_node_5_add_norm_shift_21;
  wire                _zz_pip_node_5_add_norm_shift_22;
  wire                _zz_pip_node_5_add_norm_shift_23;
  wire                _zz_pip_node_5_add_norm_shift_24;
  wire                _zz_pip_node_5_add_norm_shift_25;
  wire                _zz_pip_node_5_add_norm_shift_26;
  wire                when_FpuAdd_l101;
  wire                when_FpuAdd_l104;
  wire       [1:0]    s7_value_mode;
  wire                s7_value_quiet;
  wire                s7_value_sign;
  wire       [10:0]   s7_value_exponent;
  wire       [24:0]   s7_value_mantissa;
  wire       [27:0]   _zz_when_AFix_l856;
  reg        [24:0]   _zz_s7_value_mantissa;
  wire                when_AFix_l856;
  wire       [24:0]   s7_man;
  wire                s7_incr;
  wire       [23:0]   s7_rounded;
  wire       [11:0]   s7_exp;
  wire       [7:0]    s7_packexp;
  wire                when_GeometryFmaProbe_l72;
  `ifndef SYNTHESIS
  reg [47:0] pip_node_1_C_mode_string;
  reg [47:0] pip_node_7_add_result_RESULT_mode_string;
  reg [47:0] pip_node_6_C_mode_string;
  reg [47:0] pip_node_6_A_mode_string;
  reg [47:0] pip_node_6_add_result_RESULT_mode_string;
  reg [47:0] pip_node_5_C_mode_string;
  reg [47:0] pip_node_5_A_mode_string;
  reg [47:0] pip_node_4_C_mode_string;
  reg [47:0] pip_node_4_A_mode_string;
  reg [47:0] pip_node_3_C_mode_string;
  reg [47:0] pip_node_3_A_mode_string;
  reg [47:0] pip_node_2_A_mode_string;
  reg [47:0] pip_node_2_C_mode_string;
  reg [47:0] pip_node_1_A_mode_string;
  reg [47:0] pip_node_0_C_mode_string;
  reg [47:0] s0_ua_mode_string;
  reg [47:0] s0_ub_mode_string;
  reg [47:0] s0_uc_mode_string;
  reg [47:0] s7_value_mode_string;
  `endif


  assign _zz_s0_ua_exponent = _zz_s0_ua_exponent_1;
  assign _zz_s0_ua_exponent_1 = ($signed(_zz_s0_ua_exponent_2) - $signed(9'h07f));
  assign _zz_s0_ua_exponent_2 = _zz_s0_ua_exponent_3;
  assign _zz_s0_ua_exponent_4 = io_a[30 : 23];
  assign _zz_s0_ua_exponent_3 = {1'd0, _zz_s0_ua_exponent_4};
  assign _zz_s0_ub_exponent = _zz_s0_ub_exponent_1;
  assign _zz_s0_ub_exponent_1 = ($signed(_zz_s0_ub_exponent_2) - $signed(9'h07f));
  assign _zz_s0_ub_exponent_2 = _zz_s0_ub_exponent_3;
  assign _zz_s0_ub_exponent_4 = io_b[30 : 23];
  assign _zz_s0_ub_exponent_3 = {1'd0, _zz_s0_ub_exponent_4};
  assign _zz_s0_uc_exponent = _zz_s0_uc_exponent_1;
  assign _zz_s0_uc_exponent_1 = ($signed(_zz_s0_uc_exponent_2) - $signed(9'h07f));
  assign _zz_s0_uc_exponent_2 = _zz_s0_uc_exponent_3;
  assign _zz_s0_uc_exponent_4 = io_c[30 : 23];
  assign _zz_s0_uc_exponent_3 = {1'd0, _zz_s0_uc_exponent_4};
  assign _zz_pip_node_0_s0_EXP = _zz_pip_node_0_s0_EXP_1;
  assign _zz_pip_node_0_s0_EXP_1 = ($signed(_zz_pip_node_0_s0_EXP_2) + $signed(_zz_pip_node_0_s0_EXP_4));
  assign _zz_pip_node_0_s0_EXP_3 = s0_ua_exponent;
  assign _zz_pip_node_0_s0_EXP_2 = {{1{_zz_pip_node_0_s0_EXP_3[7]}}, _zz_pip_node_0_s0_EXP_3};
  assign _zz_pip_node_0_s0_EXP_5 = s0_ub_exponent;
  assign _zz_pip_node_0_s0_EXP_4 = {{1{_zz_pip_node_0_s0_EXP_5[7]}}, _zz_pip_node_0_s0_EXP_5};
  assign _zz_pip_node_0_C_exponent = s0_uc_exponent;
  assign _zz_pip_node_1_A_exponent = _zz_pip_node_1_A_exponent_1;
  assign _zz_pip_node_1_A_exponent_1 = _zz_pip_node_1_A_exponent_2;
  assign _zz_pip_node_1_A_exponent_2 = ($signed(_zz_pip_node_1_A_exponent_3) + $signed(_zz_pip_node_1_A_exponent_4));
  assign _zz_pip_node_1_A_exponent_3 = pip_node_1_s0_EXP;
  assign _zz_pip_node_1_A_exponent_5 = {1'b0,s1_shift};
  assign _zz_pip_node_1_A_exponent_4 = {{7{_zz_pip_node_1_A_exponent_5[1]}}, _zz_pip_node_1_A_exponent_5};
  assign _zz_pip_node_2_add_preShift_exp21 = _zz_pip_node_2_add_preShift_exp21_1;
  assign _zz_pip_node_2_add_preShift_exp21_1 = ($signed(_zz_pip_node_2_add_preShift_exp21_2) - $signed(_zz_pip_node_2_add_preShift_exp21_4));
  assign _zz_pip_node_2_add_preShift_exp21_3 = pip_node_2_C_exponent;
  assign _zz_pip_node_2_add_preShift_exp21_2 = {{2{_zz_pip_node_2_add_preShift_exp21_3[7]}}, _zz_pip_node_2_add_preShift_exp21_3};
  assign _zz_pip_node_2_add_preShift_exp21_5 = pip_node_2_A_exponent;
  assign _zz_pip_node_2_add_preShift_exp21_4 = {{1{_zz_pip_node_2_add_preShift_exp21_5[8]}}, _zz_pip_node_2_add_preShift_exp21_5};
  assign _zz_pip_node_2_add_preShift_exp12 = _zz_pip_node_2_add_preShift_exp12_1;
  assign _zz_pip_node_2_add_preShift_exp12_1 = ($signed(_zz_pip_node_2_add_preShift_exp12_2) - $signed(_zz_pip_node_2_add_preShift_exp12_4));
  assign _zz_pip_node_2_add_preShift_exp12_3 = pip_node_2_A_exponent;
  assign _zz_pip_node_2_add_preShift_exp12_2 = {{1{_zz_pip_node_2_add_preShift_exp12_3[8]}}, _zz_pip_node_2_add_preShift_exp12_3};
  assign _zz_pip_node_2_add_preShift_exp12_5 = pip_node_2_C_exponent;
  assign _zz_pip_node_2_add_preShift_exp12_4 = {{2{_zz_pip_node_2_add_preShift_exp12_5[7]}}, _zz_pip_node_2_add_preShift_exp12_5};
  assign _zz__zz_when_AFix_l1203 = pip_node_2_add_preShift_exp12;
  assign _zz__zz_when_AFix_l1203_1 = pip_node_2_add_preShift_exp21;
  assign _zz_pip_node_2_add_preShift_rs1ExponentEqual = pip_node_2_A_exponent;
  assign _zz_pip_node_2_add_preShift_rs1ExponentEqual_2 = pip_node_2_C_exponent;
  assign _zz_pip_node_2_add_preShift_rs1ExponentEqual_1 = {{1{_zz_pip_node_2_add_preShift_rs1ExponentEqual_2[7]}}, _zz_pip_node_2_add_preShift_rs1ExponentEqual_2};
  assign _zz_pip_node_2_add_preShift_rs1MantissaBigger = ({2'd0,pip_node_2_C_mantissa} <<< 2'd2);
  assign _zz_pip_node_3_add_shifter_xMantissa = (_zz_pip_node_3_add_shifter_xMantissa_1 + _zz_pip_node_3_add_shifter_xMantissa_4);
  assign _zz_pip_node_3_add_shifter_xMantissa_2 = (pip_node_3_add_preShift_absRs1Bigger ? pip_node_3_A_mantissa : _zz_pip_node_3_add_shifter_xMantissa_3);
  assign _zz_pip_node_3_add_shifter_xMantissa_1 = {1'd0, _zz_pip_node_3_add_shifter_xMantissa_2};
  assign _zz_pip_node_3_add_shifter_xMantissa_3 = ({2'd0,pip_node_3_C_mantissa} <<< 2'd2);
  assign _zz_pip_node_3_add_shifter_xMantissa_4 = ({25'd0,1'b1} <<< 5'd25);
  assign _zz_pip_node_3_add_shifter_yMantissaUnshifted = (_zz_pip_node_3_add_shifter_yMantissaUnshifted_1 + _zz_pip_node_3_add_shifter_yMantissaUnshifted_4);
  assign _zz_pip_node_3_add_shifter_yMantissaUnshifted_2 = (pip_node_3_add_preShift_absRs1Bigger ? _zz_pip_node_3_add_shifter_yMantissaUnshifted_3 : pip_node_3_A_mantissa);
  assign _zz_pip_node_3_add_shifter_yMantissaUnshifted_1 = {1'd0, _zz_pip_node_3_add_shifter_yMantissaUnshifted_2};
  assign _zz_pip_node_3_add_shifter_yMantissaUnshifted_3 = ({2'd0,pip_node_3_C_mantissa} <<< 2'd2);
  assign _zz_pip_node_3_add_shifter_yMantissaUnshifted_4 = ({25'd0,1'b1} <<< 5'd25);
  assign _zz__zz_when_Utils_l1736_3 = (_zz_when_Utils_l1736_4 >>> 1'b1);
  assign _zz__zz_when_Utils_l1736_2 = (_zz_when_Utils_l1736_3 >>> 2'b10);
  assign _zz__zz_when_Utils_l1736_1 = (_zz_when_Utils_l1736_2 >>> 3'b100);
  assign _zz__zz_when_Utils_l1736 = (_zz_when_Utils_l1736_1 >>> 4'b1000);
  assign _zz__zz_pip_node_3_add_shifter_shifter = (_zz_when_Utils_l1736 >>> 5'h10);
  assign _zz_pip_node_3_add_shifter_shifter_3 = _zz_pip_node_3_add_shifter_shifter_1;
  assign _zz_pip_node_3_add_shifter_shifter_2 = {27'd0, _zz_pip_node_3_add_shifter_shifter_3};
  assign _zz_pip_node_3_add_shifter_xyExponent = (pip_node_3_add_preShift_absRs1Bigger ? pip_node_3_A_exponent : _zz_pip_node_3_add_shifter_xyExponent_1);
  assign _zz_pip_node_3_add_shifter_xyExponent_2 = pip_node_3_C_exponent;
  assign _zz_pip_node_3_add_shifter_xyExponent_1 = {{1{_zz_pip_node_3_add_shifter_xyExponent_2[7]}}, _zz_pip_node_3_add_shifter_xyExponent_2};
  assign _zz_pip_node_4_add_math_ySigned_1 = _zz_pip_node_4_add_math_ySigned_2;
  assign _zz_pip_node_4_add_math_ySigned_2 = _zz_pip_node_4_add_math_ySigned_3;
  assign _zz_pip_node_4_add_math_ySigned_3 = ({pip_node_4_add_preShift_needSub,(pip_node_4_add_preShift_needSub ? (~ _zz_pip_node_4_add_math_ySigned) : _zz_pip_node_4_add_math_ySigned)} + _zz_pip_node_4_add_math_ySigned_4);
  assign _zz_pip_node_4_add_math_ySigned_5 = (pip_node_4_add_preShift_needSub && (! pip_node_4_add_math_roundingScrap));
  assign _zz_pip_node_4_add_math_ySigned_4 = {27'd0, _zz_pip_node_4_add_math_ySigned_5};
  assign _zz_pip_node_4_add_math_xyMantissa = _zz_pip_node_4_add_math_xyMantissa_1;
  assign _zz_pip_node_4_add_math_xyMantissa_1 = ($signed(_zz_pip_node_4_add_math_xyMantissa_2) + $signed(_zz_pip_node_4_add_math_xyMantissa_5));
  assign _zz_pip_node_4_add_math_xyMantissa_3 = {1'b0,_zz_pip_node_4_add_math_xyMantissa_4};
  assign _zz_pip_node_4_add_math_xyMantissa_2 = {{1{_zz_pip_node_4_add_math_xyMantissa_3[27]}}, _zz_pip_node_4_add_math_xyMantissa_3};
  assign _zz_pip_node_4_add_math_xyMantissa_4 = ({1'd0,pip_node_4_add_shifter_xMantissa} <<< 1'd1);
  assign _zz_pip_node_4_add_math_xyMantissa_6 = pip_node_4_add_math_ySigned;
  assign _zz_pip_node_4_add_math_xyMantissa_5 = {{1{_zz_pip_node_4_add_math_xyMantissa_6[27]}}, _zz_pip_node_4_add_math_xyMantissa_6};
  assign _zz_pip_node_6_add_result_exponent = _zz_pip_node_6_add_result_exponent_1;
  assign _zz_pip_node_6_add_result_exponent_1 = ($signed(_zz_pip_node_6_add_result_exponent_2) + $signed(_zz_pip_node_6_add_result_exponent_8));
  assign _zz_pip_node_6_add_result_exponent_2 = _zz_pip_node_6_add_result_exponent_3;
  assign _zz_pip_node_6_add_result_exponent_3 = ($signed(_zz_pip_node_6_add_result_exponent_4) - $signed(_zz_pip_node_6_add_result_exponent_6));
  assign _zz_pip_node_6_add_result_exponent_5 = pip_node_6_add_shifter_xyExponent;
  assign _zz_pip_node_6_add_result_exponent_4 = {{1{_zz_pip_node_6_add_result_exponent_5[8]}}, _zz_pip_node_6_add_result_exponent_5};
  assign _zz_pip_node_6_add_result_exponent_7 = {1'b0,pip_node_6_add_norm_shift};
  assign _zz_pip_node_6_add_result_exponent_6 = {{4{_zz_pip_node_6_add_result_exponent_7[5]}}, _zz_pip_node_6_add_result_exponent_7};
  assign _zz_pip_node_6_add_result_exponent_9 = {1'b0,1'b1};
  assign _zz_pip_node_6_add_result_exponent_8 = {{8{_zz_pip_node_6_add_result_exponent_9[1]}}, _zz_pip_node_6_add_result_exponent_9};
  assign _zz_pip_node_6_add_result_RESULT_mantissa = {pip_node_6_add_result_mantissa,pip_node_6_add_math_roundingScrap};
  assign _zz_pip_node_6_add_result_RESULT_exponent = _zz_pip_node_6_add_result_RESULT_exponent_1;
  assign _zz_pip_node_6_add_result_RESULT_exponent_1 = pip_node_6_add_result_exponent;
  assign _zz_s7_value_exponent_1 = pip_node_7_add_result_RESULT_exponent;
  assign _zz_s7_value_exponent = {{1{_zz_s7_value_exponent_1[9]}}, _zz_s7_value_exponent_1};
  assign _zz_s7_rounded_1 = {1'b0,s7_incr};
  assign _zz_s7_rounded = {22'd0, _zz_s7_rounded_1};
  assign _zz_s7_exp_1 = s7_value_exponent;
  assign _zz_s7_exp = {{1{_zz_s7_exp_1[10]}}, _zz_s7_exp_1};
  assign _zz_s7_exp_2 = _zz_s7_exp_3;
  assign _zz_s7_exp_4 = s7_rounded[23];
  assign _zz_s7_exp_3 = {11'd0, _zz_s7_exp_4};
  assign _zz_s7_packexp = ($signed(s7_exp) + $signed(12'h07f));
  assign _zz_pip_node_1_s0_EXP = pip_node_0_s0_EXP;
  assign _zz_pip_node_1_C_exponent = pip_node_0_C_exponent;
  assign _zz_pip_node_2_C_exponent = pip_node_1_C_exponent;
  assign _zz_pip_node_2_A_exponent = pip_node_1_A_exponent;
  assign _zz_pip_node_3_C_exponent = pip_node_2_C_exponent;
  assign _zz_pip_node_3_A_exponent = pip_node_2_A_exponent;
  assign _zz_pip_node_4_C_exponent = pip_node_3_C_exponent;
  assign _zz_pip_node_4_A_exponent = pip_node_3_A_exponent;
  assign _zz_pip_node_4_add_shifter_xyExponent = pip_node_3_add_shifter_xyExponent;
  assign _zz_pip_node_5_C_exponent = pip_node_4_C_exponent;
  assign _zz_pip_node_5_A_exponent = pip_node_4_A_exponent;
  assign _zz_pip_node_5_add_shifter_xyExponent = pip_node_4_add_shifter_xyExponent;
  assign _zz_pip_node_6_C_exponent = pip_node_5_C_exponent;
  assign _zz_pip_node_6_A_exponent = pip_node_5_A_exponent;
  assign _zz_pip_node_6_add_shifter_xyExponent = pip_node_5_add_shifter_xyExponent;
  assign _zz_pip_node_7_add_result_RESULT_exponent = pip_node_6_add_result_RESULT_exponent;
  assign _zz_pip_node_0_BAD = io_a[30 : 23];
  assign _zz_pip_node_0_BAD_1 = 8'h0;
  assign _zz_pip_node_0_BAD_2 = io_a[22 : 0];
  assign _zz_pip_node_0_BAD_3 = 23'h0;
  assign _zz_pip_node_0_BAD_4 = io_b[30 : 23];
  assign _zz_pip_node_0_BAD_5 = 8'h0;
  assign _zz_pip_node_0_BAD_6 = io_b[22 : 0];
  assign _zz_pip_node_0_BAD_7 = 23'h0;
  assign _zz__zz_pip_node_5_add_norm_shiftOh = pip_node_5_add_math_xyMantissa[7];
  assign _zz__zz_pip_node_5_add_norm_shiftOh_1 = pip_node_5_add_math_xyMantissa[8];
  assign _zz__zz_pip_node_5_add_norm_shiftOh_2 = {pip_node_5_add_math_xyMantissa[9],{pip_node_5_add_math_xyMantissa[10],{pip_node_5_add_math_xyMantissa[11],{pip_node_5_add_math_xyMantissa[12],{pip_node_5_add_math_xyMantissa[13],{pip_node_5_add_math_xyMantissa[14],{pip_node_5_add_math_xyMantissa[15],{pip_node_5_add_math_xyMantissa[16],{pip_node_5_add_math_xyMantissa[17],{_zz__zz_pip_node_5_add_norm_shiftOh_3,{_zz__zz_pip_node_5_add_norm_shiftOh_4,_zz__zz_pip_node_5_add_norm_shiftOh_5}}}}}}}}}}};
  assign _zz__zz_pip_node_5_add_norm_shiftOh_3 = pip_node_5_add_math_xyMantissa[18];
  assign _zz__zz_pip_node_5_add_norm_shiftOh_4 = pip_node_5_add_math_xyMantissa[19];
  assign _zz__zz_pip_node_5_add_norm_shiftOh_5 = {pip_node_5_add_math_xyMantissa[20],{pip_node_5_add_math_xyMantissa[21],{pip_node_5_add_math_xyMantissa[22],{pip_node_5_add_math_xyMantissa[23],{pip_node_5_add_math_xyMantissa[24],{pip_node_5_add_math_xyMantissa[25],{pip_node_5_add_math_xyMantissa[26],pip_node_5_add_math_xyMantissa[27]}}}}}}};
  `ifndef SYNTHESIS
  always @(*) begin
    case(pip_node_1_C_mode)
      FloatMode_ZERO : pip_node_1_C_mode_string = "ZERO  ";
      FloatMode_INF : pip_node_1_C_mode_string = "INF   ";
      FloatMode_NAN : pip_node_1_C_mode_string = "NAN   ";
      FloatMode_NORMAL : pip_node_1_C_mode_string = "NORMAL";
      default : pip_node_1_C_mode_string = "??????";
    endcase
  end
  always @(*) begin
    case(pip_node_7_add_result_RESULT_mode)
      FloatMode_ZERO : pip_node_7_add_result_RESULT_mode_string = "ZERO  ";
      FloatMode_INF : pip_node_7_add_result_RESULT_mode_string = "INF   ";
      FloatMode_NAN : pip_node_7_add_result_RESULT_mode_string = "NAN   ";
      FloatMode_NORMAL : pip_node_7_add_result_RESULT_mode_string = "NORMAL";
      default : pip_node_7_add_result_RESULT_mode_string = "??????";
    endcase
  end
  always @(*) begin
    case(pip_node_6_C_mode)
      FloatMode_ZERO : pip_node_6_C_mode_string = "ZERO  ";
      FloatMode_INF : pip_node_6_C_mode_string = "INF   ";
      FloatMode_NAN : pip_node_6_C_mode_string = "NAN   ";
      FloatMode_NORMAL : pip_node_6_C_mode_string = "NORMAL";
      default : pip_node_6_C_mode_string = "??????";
    endcase
  end
  always @(*) begin
    case(pip_node_6_A_mode)
      FloatMode_ZERO : pip_node_6_A_mode_string = "ZERO  ";
      FloatMode_INF : pip_node_6_A_mode_string = "INF   ";
      FloatMode_NAN : pip_node_6_A_mode_string = "NAN   ";
      FloatMode_NORMAL : pip_node_6_A_mode_string = "NORMAL";
      default : pip_node_6_A_mode_string = "??????";
    endcase
  end
  always @(*) begin
    case(pip_node_6_add_result_RESULT_mode)
      FloatMode_ZERO : pip_node_6_add_result_RESULT_mode_string = "ZERO  ";
      FloatMode_INF : pip_node_6_add_result_RESULT_mode_string = "INF   ";
      FloatMode_NAN : pip_node_6_add_result_RESULT_mode_string = "NAN   ";
      FloatMode_NORMAL : pip_node_6_add_result_RESULT_mode_string = "NORMAL";
      default : pip_node_6_add_result_RESULT_mode_string = "??????";
    endcase
  end
  always @(*) begin
    case(pip_node_5_C_mode)
      FloatMode_ZERO : pip_node_5_C_mode_string = "ZERO  ";
      FloatMode_INF : pip_node_5_C_mode_string = "INF   ";
      FloatMode_NAN : pip_node_5_C_mode_string = "NAN   ";
      FloatMode_NORMAL : pip_node_5_C_mode_string = "NORMAL";
      default : pip_node_5_C_mode_string = "??????";
    endcase
  end
  always @(*) begin
    case(pip_node_5_A_mode)
      FloatMode_ZERO : pip_node_5_A_mode_string = "ZERO  ";
      FloatMode_INF : pip_node_5_A_mode_string = "INF   ";
      FloatMode_NAN : pip_node_5_A_mode_string = "NAN   ";
      FloatMode_NORMAL : pip_node_5_A_mode_string = "NORMAL";
      default : pip_node_5_A_mode_string = "??????";
    endcase
  end
  always @(*) begin
    case(pip_node_4_C_mode)
      FloatMode_ZERO : pip_node_4_C_mode_string = "ZERO  ";
      FloatMode_INF : pip_node_4_C_mode_string = "INF   ";
      FloatMode_NAN : pip_node_4_C_mode_string = "NAN   ";
      FloatMode_NORMAL : pip_node_4_C_mode_string = "NORMAL";
      default : pip_node_4_C_mode_string = "??????";
    endcase
  end
  always @(*) begin
    case(pip_node_4_A_mode)
      FloatMode_ZERO : pip_node_4_A_mode_string = "ZERO  ";
      FloatMode_INF : pip_node_4_A_mode_string = "INF   ";
      FloatMode_NAN : pip_node_4_A_mode_string = "NAN   ";
      FloatMode_NORMAL : pip_node_4_A_mode_string = "NORMAL";
      default : pip_node_4_A_mode_string = "??????";
    endcase
  end
  always @(*) begin
    case(pip_node_3_C_mode)
      FloatMode_ZERO : pip_node_3_C_mode_string = "ZERO  ";
      FloatMode_INF : pip_node_3_C_mode_string = "INF   ";
      FloatMode_NAN : pip_node_3_C_mode_string = "NAN   ";
      FloatMode_NORMAL : pip_node_3_C_mode_string = "NORMAL";
      default : pip_node_3_C_mode_string = "??????";
    endcase
  end
  always @(*) begin
    case(pip_node_3_A_mode)
      FloatMode_ZERO : pip_node_3_A_mode_string = "ZERO  ";
      FloatMode_INF : pip_node_3_A_mode_string = "INF   ";
      FloatMode_NAN : pip_node_3_A_mode_string = "NAN   ";
      FloatMode_NORMAL : pip_node_3_A_mode_string = "NORMAL";
      default : pip_node_3_A_mode_string = "??????";
    endcase
  end
  always @(*) begin
    case(pip_node_2_A_mode)
      FloatMode_ZERO : pip_node_2_A_mode_string = "ZERO  ";
      FloatMode_INF : pip_node_2_A_mode_string = "INF   ";
      FloatMode_NAN : pip_node_2_A_mode_string = "NAN   ";
      FloatMode_NORMAL : pip_node_2_A_mode_string = "NORMAL";
      default : pip_node_2_A_mode_string = "??????";
    endcase
  end
  always @(*) begin
    case(pip_node_2_C_mode)
      FloatMode_ZERO : pip_node_2_C_mode_string = "ZERO  ";
      FloatMode_INF : pip_node_2_C_mode_string = "INF   ";
      FloatMode_NAN : pip_node_2_C_mode_string = "NAN   ";
      FloatMode_NORMAL : pip_node_2_C_mode_string = "NORMAL";
      default : pip_node_2_C_mode_string = "??????";
    endcase
  end
  always @(*) begin
    case(pip_node_1_A_mode)
      FloatMode_ZERO : pip_node_1_A_mode_string = "ZERO  ";
      FloatMode_INF : pip_node_1_A_mode_string = "INF   ";
      FloatMode_NAN : pip_node_1_A_mode_string = "NAN   ";
      FloatMode_NORMAL : pip_node_1_A_mode_string = "NORMAL";
      default : pip_node_1_A_mode_string = "??????";
    endcase
  end
  always @(*) begin
    case(pip_node_0_C_mode)
      FloatMode_ZERO : pip_node_0_C_mode_string = "ZERO  ";
      FloatMode_INF : pip_node_0_C_mode_string = "INF   ";
      FloatMode_NAN : pip_node_0_C_mode_string = "NAN   ";
      FloatMode_NORMAL : pip_node_0_C_mode_string = "NORMAL";
      default : pip_node_0_C_mode_string = "??????";
    endcase
  end
  always @(*) begin
    case(s0_ua_mode)
      FloatMode_ZERO : s0_ua_mode_string = "ZERO  ";
      FloatMode_INF : s0_ua_mode_string = "INF   ";
      FloatMode_NAN : s0_ua_mode_string = "NAN   ";
      FloatMode_NORMAL : s0_ua_mode_string = "NORMAL";
      default : s0_ua_mode_string = "??????";
    endcase
  end
  always @(*) begin
    case(s0_ub_mode)
      FloatMode_ZERO : s0_ub_mode_string = "ZERO  ";
      FloatMode_INF : s0_ub_mode_string = "INF   ";
      FloatMode_NAN : s0_ub_mode_string = "NAN   ";
      FloatMode_NORMAL : s0_ub_mode_string = "NORMAL";
      default : s0_ub_mode_string = "??????";
    endcase
  end
  always @(*) begin
    case(s0_uc_mode)
      FloatMode_ZERO : s0_uc_mode_string = "ZERO  ";
      FloatMode_INF : s0_uc_mode_string = "INF   ";
      FloatMode_NAN : s0_uc_mode_string = "NAN   ";
      FloatMode_NORMAL : s0_uc_mode_string = "NORMAL";
      default : s0_uc_mode_string = "??????";
    endcase
  end
  always @(*) begin
    case(s7_value_mode)
      FloatMode_ZERO : s7_value_mode_string = "ZERO  ";
      FloatMode_INF : s7_value_mode_string = "INF   ";
      FloatMode_NAN : s7_value_mode_string = "NAN   ";
      FloatMode_NORMAL : s7_value_mode_string = "NORMAL";
      default : s7_value_mode_string = "??????";
    endcase
  end
  `endif

  always @(*) begin
    _zz_pip_node_3_add_shifter_shifter = _zz_when_Utils_l1736;
    _zz_pip_node_3_add_shifter_shifter = (pip_node_3_add_preShift_expDifAbsSat[4] ? _zz__zz_pip_node_3_add_shifter_shifter : _zz_when_Utils_l1736);
  end

  always @(*) begin
    _zz_when_Utils_l1736 = _zz_when_Utils_l1736_1;
    _zz_when_Utils_l1736 = (pip_node_3_add_preShift_expDifAbsSat[3] ? _zz__zz_when_Utils_l1736 : _zz_when_Utils_l1736_1);
  end

  always @(*) begin
    _zz_when_Utils_l1736_1 = _zz_when_Utils_l1736_2;
    _zz_when_Utils_l1736_1 = (pip_node_3_add_preShift_expDifAbsSat[2] ? _zz__zz_when_Utils_l1736_1 : _zz_when_Utils_l1736_2);
  end

  always @(*) begin
    _zz_when_Utils_l1736_2 = _zz_when_Utils_l1736_3;
    _zz_when_Utils_l1736_2 = (pip_node_3_add_preShift_expDifAbsSat[1] ? _zz__zz_when_Utils_l1736_2 : _zz_when_Utils_l1736_3);
  end

  always @(*) begin
    _zz_when_Utils_l1736_3 = _zz_when_Utils_l1736_4;
    _zz_when_Utils_l1736_3 = (pip_node_3_add_preShift_expDifAbsSat[0] ? _zz__zz_when_Utils_l1736_3 : _zz_when_Utils_l1736_4);
  end

  assign pip_node_0_valid = io_valid;
  assign s0_ua_sign = io_a[31];
  assign s0_ua_quiet = 1'b1;
  assign s0_ua_exponent = _zz_s0_ua_exponent[7:0];
  assign s0_ua_mantissa = io_a[22 : 0];
  always @(*) begin
    s0_ua_mode = FloatMode_NORMAL;
    if(when_GeometryFmaProbe_l31) begin
      s0_ua_mode = FloatMode_ZERO;
    end
  end

  assign when_GeometryFmaProbe_l31 = (io_a[30 : 0] == 31'h0);
  assign s0_ub_sign = io_b[31];
  assign s0_ub_quiet = 1'b1;
  assign s0_ub_exponent = _zz_s0_ub_exponent[7:0];
  assign s0_ub_mantissa = io_b[22 : 0];
  always @(*) begin
    s0_ub_mode = FloatMode_NORMAL;
    if(when_GeometryFmaProbe_l31_1) begin
      s0_ub_mode = FloatMode_ZERO;
    end
  end

  assign when_GeometryFmaProbe_l31_1 = (io_b[30 : 0] == 31'h0);
  assign s0_uc_sign = io_c[31];
  assign s0_uc_quiet = 1'b1;
  assign s0_uc_exponent = _zz_s0_uc_exponent[7:0];
  assign s0_uc_mantissa = io_c[22 : 0];
  always @(*) begin
    s0_uc_mode = FloatMode_NORMAL;
    if(when_GeometryFmaProbe_l31_2) begin
      s0_uc_mode = FloatMode_ZERO;
    end
  end

  assign when_GeometryFmaProbe_l31_2 = (io_c[30 : 0] == 31'h0);
  assign pip_node_0_s0_MUL = ({1'b1,io_a[22 : 0]} * {1'b1,io_b[22 : 0]});
  assign pip_node_0_s0_EXP = _zz_pip_node_0_s0_EXP;
  assign pip_node_0_s0_SIGN = (s0_ua_sign ^ s0_ub_sign);
  assign pip_node_0_s0_ZERO = ((s0_ua_mode == FloatMode_ZERO) || (s0_ub_mode == FloatMode_ZERO));
  assign pip_node_0_s0_MONLY = io_multiplyOnly;
  always @(*) begin
    pip_node_0_C_mode = s0_uc_mode;
    if(io_multiplyOnly) begin
      pip_node_0_C_mode = FloatMode_ZERO;
    end
  end

  assign pip_node_0_C_quiet = s0_uc_quiet;
  always @(*) begin
    pip_node_0_C_sign = s0_uc_sign;
    if(io_multiplyOnly) begin
      pip_node_0_C_sign = 1'b0;
    end
  end

  assign pip_node_0_C_exponent = _zz_pip_node_0_C_exponent;
  assign pip_node_0_C_mantissa = s0_uc_mantissa;
  assign pip_node_0_BAD = ((((io_a[30 : 23] == 8'hff) || ((_zz_pip_node_0_BAD == _zz_pip_node_0_BAD_1) && (_zz_pip_node_0_BAD_2 != _zz_pip_node_0_BAD_3))) || ((io_b[30 : 23] == 8'hff) || ((_zz_pip_node_0_BAD_4 == _zz_pip_node_0_BAD_5) && (_zz_pip_node_0_BAD_6 != _zz_pip_node_0_BAD_7)))) || ((io_c[30 : 23] == 8'hff) || ((io_c[30 : 23] == 8'h0) && (io_c[22 : 0] != 23'h0))));
  assign pip_node_0_RD = 1'b0;
  assign s1_shift = pip_node_1_s0_MUL[47];
  assign s1_normalized = (s1_shift ? pip_node_1_s0_MUL[46 : 0] : {pip_node_1_s0_MUL[45 : 0],1'b0});
  assign pip_node_1_A_sign = pip_node_1_s0_SIGN;
  assign pip_node_1_A_exponent = _zz_pip_node_1_A_exponent;
  always @(*) begin
    pip_node_1_A_mantissa = s1_normalized[46 : 22];
    if(when_GeometryFmaProbe_l55) begin
      pip_node_1_A_mantissa[0] = 1'b1;
    end
  end

  assign when_GeometryFmaProbe_l55 = (pip_node_1_s0_MONLY && (|s1_normalized[21 : 0]));
  always @(*) begin
    pip_node_1_A_mode = FloatMode_NORMAL;
    if(pip_node_1_s0_ZERO) begin
      pip_node_1_A_mode = FloatMode_ZERO;
    end
  end

  assign pip_node_1_A_quiet = 1'b1;
  assign pip_node_2_add_preShift_exp21 = _zz_pip_node_2_add_preShift_exp21;
  assign pip_node_2_add_preShift_exp12 = _zz_pip_node_2_add_preShift_exp12;
  assign _zz_when_AFix_l1203 = (pip_node_2_add_preShift_exp21[9] ? _zz__zz_when_AFix_l1203 : _zz__zz_when_AFix_l1203_1);
  assign when_AFix_l1203 = _zz_when_AFix_l1203[9];
  always @(*) begin
    if(when_AFix_l1203) begin
      _zz_pip_node_2_add_preShift_expDifAbs = 10'h0;
    end else begin
      _zz_pip_node_2_add_preShift_expDifAbs = _zz_when_AFix_l1203;
    end
  end

  assign pip_node_2_add_preShift_expDifAbs = _zz_pip_node_2_add_preShift_expDifAbs[8 : 0];
  assign pip_node_2_add_preShift_rs1ExponentBigger = ((pip_node_2_add_preShift_exp21[9] || (pip_node_2_C_mode == FloatMode_ZERO)) && (! (pip_node_2_A_mode == FloatMode_ZERO)));
  assign pip_node_2_add_preShift_rs1ExponentEqual = ($signed(_zz_pip_node_2_add_preShift_rs1ExponentEqual) == $signed(_zz_pip_node_2_add_preShift_rs1ExponentEqual_1));
  assign pip_node_2_add_preShift_rs1MantissaBigger = (_zz_pip_node_2_add_preShift_rs1MantissaBigger < pip_node_2_A_mantissa);
  assign pip_node_2_add_preShift_absRs1Bigger = ((((pip_node_2_add_preShift_rs1ExponentBigger || (pip_node_2_add_preShift_rs1ExponentEqual && pip_node_2_add_preShift_rs1MantissaBigger)) && (! (pip_node_2_A_mode == FloatMode_ZERO))) || (pip_node_2_A_mode == FloatMode_INF)) && (! (pip_node_2_C_mode == FloatMode_INF)));
  assign pip_node_2_add_preShift_needSub = (pip_node_2_A_sign ^ pip_node_2_C_sign);
  assign pip_node_2_add_preShift_passThrough = ((pip_node_2_A_mode == FloatMode_ZERO) || (pip_node_2_C_mode == FloatMode_ZERO));
  assign when_UInt_l119 = (|pip_node_2_add_preShift_expDifAbs[8 : 5]);
  always @(*) begin
    if(when_UInt_l119) begin
      _zz_pip_node_2_add_preShift_expDifAbsSat = 5'h1f;
    end else begin
      _zz_pip_node_2_add_preShift_expDifAbsSat = pip_node_2_add_preShift_expDifAbs[4 : 0];
    end
  end

  assign pip_node_2_add_preShift_expDifAbsSat = (pip_node_2_add_preShift_passThrough ? 5'h1f : _zz_pip_node_2_add_preShift_expDifAbsSat);
  assign pip_node_3_add_shifter_xySign = (pip_node_3_add_preShift_absRs1Bigger ? pip_node_3_A_sign : pip_node_3_C_sign);
  assign pip_node_3_add_shifter_xMantissa = _zz_pip_node_3_add_shifter_xMantissa;
  assign pip_node_3_add_shifter_yMantissaUnshifted = _zz_pip_node_3_add_shifter_yMantissaUnshifted;
  assign _zz_when_Utils_l1736_4 = {{pip_node_3_add_shifter_yMantissaUnshifted,1'b0},1'b0};
  always @(*) begin
    _zz_pip_node_3_add_shifter_shifter_1 = 1'b0;
    if(when_Utils_l1736) begin
      _zz_pip_node_3_add_shifter_shifter_1 = 1'b1;
    end
    if(when_Utils_l1736_1) begin
      _zz_pip_node_3_add_shifter_shifter_1 = 1'b1;
    end
    if(when_Utils_l1736_2) begin
      _zz_pip_node_3_add_shifter_shifter_1 = 1'b1;
    end
    if(when_Utils_l1736_3) begin
      _zz_pip_node_3_add_shifter_shifter_1 = 1'b1;
    end
    if(when_Utils_l1736_4) begin
      _zz_pip_node_3_add_shifter_shifter_1 = 1'b1;
    end
  end

  assign when_Utils_l1736 = (pip_node_3_add_preShift_expDifAbsSat[0] && (_zz_when_Utils_l1736_4[0 : 0] != 1'b0));
  assign when_Utils_l1736_1 = (pip_node_3_add_preShift_expDifAbsSat[1] && (_zz_when_Utils_l1736_3[1 : 0] != 2'b00));
  assign when_Utils_l1736_2 = (pip_node_3_add_preShift_expDifAbsSat[2] && (_zz_when_Utils_l1736_2[3 : 0] != 4'b0000));
  assign when_Utils_l1736_3 = (pip_node_3_add_preShift_expDifAbsSat[3] && (_zz_when_Utils_l1736_1[7 : 0] != 8'h0));
  assign when_Utils_l1736_4 = (pip_node_3_add_preShift_expDifAbsSat[4] && (_zz_when_Utils_l1736[15 : 0] != 16'h0));
  assign pip_node_3_add_shifter_shifter = (_zz_pip_node_3_add_shifter_shifter | _zz_pip_node_3_add_shifter_shifter_2);
  assign pip_node_3_add_shifter_yMantissa = pip_node_3_add_shifter_shifter[27 : 1];
  assign pip_node_3_add_shifter_xyExponent = _zz_pip_node_3_add_shifter_xyExponent;
  always @(*) begin
    pip_node_4_add_math_roundingScrap = (pip_node_4_add_shifter_shifter[0] && (! pip_node_4_add_preShift_passThrough));
    if(when_FpuAdd_l56) begin
      pip_node_4_add_math_roundingScrap = 1'b0;
    end
  end

  assign when_FpuAdd_l56 = ((! (pip_node_4_A_mode == FloatMode_NORMAL)) || (! (pip_node_4_C_mode == FloatMode_NORMAL)));
  assign _zz_pip_node_4_add_math_ySigned = pip_node_4_add_shifter_yMantissa;
  assign pip_node_4_add_math_ySigned = _zz_pip_node_4_add_math_ySigned_1;
  assign pip_node_4_add_math_xyMantissa = _zz_pip_node_4_add_math_xyMantissa[27:0];
  assign _zz_pip_node_5_add_norm_shiftOh = {pip_node_5_add_math_xyMantissa[0],{pip_node_5_add_math_xyMantissa[1],{pip_node_5_add_math_xyMantissa[2],{pip_node_5_add_math_xyMantissa[3],{pip_node_5_add_math_xyMantissa[4],{pip_node_5_add_math_xyMantissa[5],{pip_node_5_add_math_xyMantissa[6],{_zz__zz_pip_node_5_add_norm_shiftOh,{_zz__zz_pip_node_5_add_norm_shiftOh_1,_zz__zz_pip_node_5_add_norm_shiftOh_2}}}}}}}}};
  assign _zz_pip_node_5_add_norm_shiftOh_1 = _zz_pip_node_5_add_norm_shiftOh[0];
  assign _zz_pip_node_5_add_norm_shiftOh_2 = _zz_pip_node_5_add_norm_shiftOh[1];
  assign _zz_pip_node_5_add_norm_shiftOh_3 = _zz_pip_node_5_add_norm_shiftOh[2];
  assign _zz_pip_node_5_add_norm_shiftOh_4 = _zz_pip_node_5_add_norm_shiftOh[3];
  assign _zz_pip_node_5_add_norm_shiftOh_5 = _zz_pip_node_5_add_norm_shiftOh[4];
  assign _zz_pip_node_5_add_norm_shiftOh_6 = _zz_pip_node_5_add_norm_shiftOh[5];
  assign _zz_pip_node_5_add_norm_shiftOh_7 = _zz_pip_node_5_add_norm_shiftOh[6];
  assign _zz_pip_node_5_add_norm_shiftOh_8 = _zz_pip_node_5_add_norm_shiftOh[7];
  assign _zz_pip_node_5_add_norm_shiftOh_9 = _zz_pip_node_5_add_norm_shiftOh[8];
  assign _zz_pip_node_5_add_norm_shiftOh_10 = _zz_pip_node_5_add_norm_shiftOh[9];
  assign _zz_pip_node_5_add_norm_shiftOh_11 = _zz_pip_node_5_add_norm_shiftOh[10];
  assign _zz_pip_node_5_add_norm_shiftOh_12 = _zz_pip_node_5_add_norm_shiftOh[11];
  assign _zz_pip_node_5_add_norm_shiftOh_13 = _zz_pip_node_5_add_norm_shiftOh[12];
  assign _zz_pip_node_5_add_norm_shiftOh_14 = _zz_pip_node_5_add_norm_shiftOh[13];
  assign _zz_pip_node_5_add_norm_shiftOh_15 = _zz_pip_node_5_add_norm_shiftOh[14];
  assign _zz_pip_node_5_add_norm_shiftOh_16 = _zz_pip_node_5_add_norm_shiftOh[15];
  assign _zz_pip_node_5_add_norm_shiftOh_17 = _zz_pip_node_5_add_norm_shiftOh[16];
  assign _zz_pip_node_5_add_norm_shiftOh_18 = _zz_pip_node_5_add_norm_shiftOh[17];
  assign _zz_pip_node_5_add_norm_shiftOh_19 = _zz_pip_node_5_add_norm_shiftOh[18];
  assign _zz_pip_node_5_add_norm_shiftOh_20 = _zz_pip_node_5_add_norm_shiftOh[19];
  assign _zz_pip_node_5_add_norm_shiftOh_21 = _zz_pip_node_5_add_norm_shiftOh[20];
  assign _zz_pip_node_5_add_norm_shiftOh_22 = _zz_pip_node_5_add_norm_shiftOh[21];
  assign _zz_pip_node_5_add_norm_shiftOh_23 = _zz_pip_node_5_add_norm_shiftOh[22];
  assign _zz_pip_node_5_add_norm_shiftOh_24 = _zz_pip_node_5_add_norm_shiftOh[23];
  assign _zz_pip_node_5_add_norm_shiftOh_25 = _zz_pip_node_5_add_norm_shiftOh[24];
  assign _zz_pip_node_5_add_norm_shiftOh_26 = _zz_pip_node_5_add_norm_shiftOh[25];
  assign _zz_pip_node_5_add_norm_shiftOh_27 = _zz_pip_node_5_add_norm_shiftOh[26];
  always @(*) begin
    _zz_pip_node_5_add_norm_shiftOh_28[0] = (_zz_pip_node_5_add_norm_shiftOh_1 && (! 1'b0));
    _zz_pip_node_5_add_norm_shiftOh_28[1] = (_zz_pip_node_5_add_norm_shiftOh_2 && (! _zz_pip_node_5_add_norm_shiftOh_1));
    _zz_pip_node_5_add_norm_shiftOh_28[2] = (_zz_pip_node_5_add_norm_shiftOh_3 && (! (|{_zz_pip_node_5_add_norm_shiftOh_2,_zz_pip_node_5_add_norm_shiftOh_1})));
    _zz_pip_node_5_add_norm_shiftOh_28[3] = (_zz_pip_node_5_add_norm_shiftOh_4 && (! (|{_zz_pip_node_5_add_norm_shiftOh_3,{_zz_pip_node_5_add_norm_shiftOh_2,_zz_pip_node_5_add_norm_shiftOh_1}})));
    _zz_pip_node_5_add_norm_shiftOh_28[4] = (_zz_pip_node_5_add_norm_shiftOh_5 && (! _zz_pip_node_5_add_norm_shiftOh_29));
    _zz_pip_node_5_add_norm_shiftOh_28[5] = (_zz_pip_node_5_add_norm_shiftOh_6 && (! (_zz_pip_node_5_add_norm_shiftOh_5 || _zz_pip_node_5_add_norm_shiftOh_29)));
    _zz_pip_node_5_add_norm_shiftOh_28[6] = (_zz_pip_node_5_add_norm_shiftOh_7 && (! ((|{_zz_pip_node_5_add_norm_shiftOh_6,_zz_pip_node_5_add_norm_shiftOh_5}) || _zz_pip_node_5_add_norm_shiftOh_29)));
    _zz_pip_node_5_add_norm_shiftOh_28[7] = (_zz_pip_node_5_add_norm_shiftOh_8 && (! ((|{_zz_pip_node_5_add_norm_shiftOh_7,{_zz_pip_node_5_add_norm_shiftOh_6,_zz_pip_node_5_add_norm_shiftOh_5}}) || _zz_pip_node_5_add_norm_shiftOh_29)));
    _zz_pip_node_5_add_norm_shiftOh_28[8] = (_zz_pip_node_5_add_norm_shiftOh_9 && (! (_zz_pip_node_5_add_norm_shiftOh_30 || _zz_pip_node_5_add_norm_shiftOh_29)));
    _zz_pip_node_5_add_norm_shiftOh_28[9] = (_zz_pip_node_5_add_norm_shiftOh_10 && (! (_zz_pip_node_5_add_norm_shiftOh_9 || _zz_pip_node_5_add_norm_shiftOh_31)));
    _zz_pip_node_5_add_norm_shiftOh_28[10] = (_zz_pip_node_5_add_norm_shiftOh_11 && (! ((|{_zz_pip_node_5_add_norm_shiftOh_10,_zz_pip_node_5_add_norm_shiftOh_9}) || _zz_pip_node_5_add_norm_shiftOh_31)));
    _zz_pip_node_5_add_norm_shiftOh_28[11] = (_zz_pip_node_5_add_norm_shiftOh_12 && (! ((|{_zz_pip_node_5_add_norm_shiftOh_11,{_zz_pip_node_5_add_norm_shiftOh_10,_zz_pip_node_5_add_norm_shiftOh_9}}) || _zz_pip_node_5_add_norm_shiftOh_31)));
    _zz_pip_node_5_add_norm_shiftOh_28[12] = (_zz_pip_node_5_add_norm_shiftOh_13 && (! (_zz_pip_node_5_add_norm_shiftOh_32 || _zz_pip_node_5_add_norm_shiftOh_31)));
    _zz_pip_node_5_add_norm_shiftOh_28[13] = (_zz_pip_node_5_add_norm_shiftOh_14 && (! (_zz_pip_node_5_add_norm_shiftOh_13 || _zz_pip_node_5_add_norm_shiftOh_33)));
    _zz_pip_node_5_add_norm_shiftOh_28[14] = (_zz_pip_node_5_add_norm_shiftOh_15 && (! ((|{_zz_pip_node_5_add_norm_shiftOh_14,_zz_pip_node_5_add_norm_shiftOh_13}) || _zz_pip_node_5_add_norm_shiftOh_33)));
    _zz_pip_node_5_add_norm_shiftOh_28[15] = (_zz_pip_node_5_add_norm_shiftOh_16 && (! ((|{_zz_pip_node_5_add_norm_shiftOh_15,{_zz_pip_node_5_add_norm_shiftOh_14,_zz_pip_node_5_add_norm_shiftOh_13}}) || _zz_pip_node_5_add_norm_shiftOh_33)));
    _zz_pip_node_5_add_norm_shiftOh_28[16] = (_zz_pip_node_5_add_norm_shiftOh_17 && (! (_zz_pip_node_5_add_norm_shiftOh_34 || _zz_pip_node_5_add_norm_shiftOh_33)));
    _zz_pip_node_5_add_norm_shiftOh_28[17] = (_zz_pip_node_5_add_norm_shiftOh_18 && (! (_zz_pip_node_5_add_norm_shiftOh_17 || _zz_pip_node_5_add_norm_shiftOh_35)));
    _zz_pip_node_5_add_norm_shiftOh_28[18] = (_zz_pip_node_5_add_norm_shiftOh_19 && (! ((|{_zz_pip_node_5_add_norm_shiftOh_18,_zz_pip_node_5_add_norm_shiftOh_17}) || _zz_pip_node_5_add_norm_shiftOh_35)));
    _zz_pip_node_5_add_norm_shiftOh_28[19] = (_zz_pip_node_5_add_norm_shiftOh_20 && (! ((|{_zz_pip_node_5_add_norm_shiftOh_19,{_zz_pip_node_5_add_norm_shiftOh_18,_zz_pip_node_5_add_norm_shiftOh_17}}) || _zz_pip_node_5_add_norm_shiftOh_35)));
    _zz_pip_node_5_add_norm_shiftOh_28[20] = (_zz_pip_node_5_add_norm_shiftOh_21 && (! (_zz_pip_node_5_add_norm_shiftOh_36 || _zz_pip_node_5_add_norm_shiftOh_35)));
    _zz_pip_node_5_add_norm_shiftOh_28[21] = (_zz_pip_node_5_add_norm_shiftOh_22 && (! (_zz_pip_node_5_add_norm_shiftOh_21 || (_zz_pip_node_5_add_norm_shiftOh_36 || _zz_pip_node_5_add_norm_shiftOh_35))));
    _zz_pip_node_5_add_norm_shiftOh_28[22] = (_zz_pip_node_5_add_norm_shiftOh_23 && (! ((|{_zz_pip_node_5_add_norm_shiftOh_22,_zz_pip_node_5_add_norm_shiftOh_21}) || (_zz_pip_node_5_add_norm_shiftOh_36 || _zz_pip_node_5_add_norm_shiftOh_35))));
    _zz_pip_node_5_add_norm_shiftOh_28[23] = (_zz_pip_node_5_add_norm_shiftOh_24 && (! ((|{_zz_pip_node_5_add_norm_shiftOh_23,{_zz_pip_node_5_add_norm_shiftOh_22,_zz_pip_node_5_add_norm_shiftOh_21}}) || (_zz_pip_node_5_add_norm_shiftOh_36 || _zz_pip_node_5_add_norm_shiftOh_35))));
    _zz_pip_node_5_add_norm_shiftOh_28[24] = (_zz_pip_node_5_add_norm_shiftOh_25 && (! (_zz_pip_node_5_add_norm_shiftOh_37 || (_zz_pip_node_5_add_norm_shiftOh_36 || _zz_pip_node_5_add_norm_shiftOh_35))));
    _zz_pip_node_5_add_norm_shiftOh_28[25] = (_zz_pip_node_5_add_norm_shiftOh_26 && (! (_zz_pip_node_5_add_norm_shiftOh_25 || (_zz_pip_node_5_add_norm_shiftOh_38 || _zz_pip_node_5_add_norm_shiftOh_35))));
    _zz_pip_node_5_add_norm_shiftOh_28[26] = (_zz_pip_node_5_add_norm_shiftOh_27 && (! ((|{_zz_pip_node_5_add_norm_shiftOh_26,_zz_pip_node_5_add_norm_shiftOh_25}) || (_zz_pip_node_5_add_norm_shiftOh_38 || _zz_pip_node_5_add_norm_shiftOh_35))));
    _zz_pip_node_5_add_norm_shiftOh_28[27] = (_zz_pip_node_5_add_norm_shiftOh[27] && (! ((|{_zz_pip_node_5_add_norm_shiftOh_27,{_zz_pip_node_5_add_norm_shiftOh_26,_zz_pip_node_5_add_norm_shiftOh_25}}) || (_zz_pip_node_5_add_norm_shiftOh_38 || _zz_pip_node_5_add_norm_shiftOh_35))));
  end

  assign _zz_pip_node_5_add_norm_shiftOh_29 = (|{_zz_pip_node_5_add_norm_shiftOh_4,{_zz_pip_node_5_add_norm_shiftOh_3,{_zz_pip_node_5_add_norm_shiftOh_2,_zz_pip_node_5_add_norm_shiftOh_1}}});
  assign _zz_pip_node_5_add_norm_shiftOh_30 = (|{_zz_pip_node_5_add_norm_shiftOh_8,{_zz_pip_node_5_add_norm_shiftOh_7,{_zz_pip_node_5_add_norm_shiftOh_6,_zz_pip_node_5_add_norm_shiftOh_5}}});
  assign _zz_pip_node_5_add_norm_shiftOh_31 = (|{_zz_pip_node_5_add_norm_shiftOh_30,_zz_pip_node_5_add_norm_shiftOh_29});
  assign _zz_pip_node_5_add_norm_shiftOh_32 = (|{_zz_pip_node_5_add_norm_shiftOh_12,{_zz_pip_node_5_add_norm_shiftOh_11,{_zz_pip_node_5_add_norm_shiftOh_10,_zz_pip_node_5_add_norm_shiftOh_9}}});
  assign _zz_pip_node_5_add_norm_shiftOh_33 = (|{_zz_pip_node_5_add_norm_shiftOh_32,{_zz_pip_node_5_add_norm_shiftOh_30,_zz_pip_node_5_add_norm_shiftOh_29}});
  assign _zz_pip_node_5_add_norm_shiftOh_34 = (|{_zz_pip_node_5_add_norm_shiftOh_16,{_zz_pip_node_5_add_norm_shiftOh_15,{_zz_pip_node_5_add_norm_shiftOh_14,_zz_pip_node_5_add_norm_shiftOh_13}}});
  assign _zz_pip_node_5_add_norm_shiftOh_35 = (|{_zz_pip_node_5_add_norm_shiftOh_34,{_zz_pip_node_5_add_norm_shiftOh_32,{_zz_pip_node_5_add_norm_shiftOh_30,_zz_pip_node_5_add_norm_shiftOh_29}}});
  assign _zz_pip_node_5_add_norm_shiftOh_36 = (|{_zz_pip_node_5_add_norm_shiftOh_20,{_zz_pip_node_5_add_norm_shiftOh_19,{_zz_pip_node_5_add_norm_shiftOh_18,_zz_pip_node_5_add_norm_shiftOh_17}}});
  assign _zz_pip_node_5_add_norm_shiftOh_37 = (|{_zz_pip_node_5_add_norm_shiftOh_24,{_zz_pip_node_5_add_norm_shiftOh_23,{_zz_pip_node_5_add_norm_shiftOh_22,_zz_pip_node_5_add_norm_shiftOh_21}}});
  assign _zz_pip_node_5_add_norm_shiftOh_38 = (|{_zz_pip_node_5_add_norm_shiftOh_37,_zz_pip_node_5_add_norm_shiftOh_36});
  assign pip_node_5_add_norm_shiftOh = _zz_pip_node_5_add_norm_shiftOh_28;
  assign _zz_pip_node_5_add_norm_shift = pip_node_5_add_norm_shiftOh[3];
  assign _zz_pip_node_5_add_norm_shift_1 = pip_node_5_add_norm_shiftOh[5];
  assign _zz_pip_node_5_add_norm_shift_2 = pip_node_5_add_norm_shiftOh[6];
  assign _zz_pip_node_5_add_norm_shift_3 = pip_node_5_add_norm_shiftOh[7];
  assign _zz_pip_node_5_add_norm_shift_4 = pip_node_5_add_norm_shiftOh[9];
  assign _zz_pip_node_5_add_norm_shift_5 = pip_node_5_add_norm_shiftOh[10];
  assign _zz_pip_node_5_add_norm_shift_6 = pip_node_5_add_norm_shiftOh[11];
  assign _zz_pip_node_5_add_norm_shift_7 = pip_node_5_add_norm_shiftOh[12];
  assign _zz_pip_node_5_add_norm_shift_8 = pip_node_5_add_norm_shiftOh[13];
  assign _zz_pip_node_5_add_norm_shift_9 = pip_node_5_add_norm_shiftOh[14];
  assign _zz_pip_node_5_add_norm_shift_10 = pip_node_5_add_norm_shiftOh[15];
  assign _zz_pip_node_5_add_norm_shift_11 = pip_node_5_add_norm_shiftOh[17];
  assign _zz_pip_node_5_add_norm_shift_12 = pip_node_5_add_norm_shiftOh[18];
  assign _zz_pip_node_5_add_norm_shift_13 = pip_node_5_add_norm_shiftOh[19];
  assign _zz_pip_node_5_add_norm_shift_14 = pip_node_5_add_norm_shiftOh[20];
  assign _zz_pip_node_5_add_norm_shift_15 = pip_node_5_add_norm_shiftOh[21];
  assign _zz_pip_node_5_add_norm_shift_16 = pip_node_5_add_norm_shiftOh[22];
  assign _zz_pip_node_5_add_norm_shift_17 = pip_node_5_add_norm_shiftOh[23];
  assign _zz_pip_node_5_add_norm_shift_18 = pip_node_5_add_norm_shiftOh[24];
  assign _zz_pip_node_5_add_norm_shift_19 = pip_node_5_add_norm_shiftOh[25];
  assign _zz_pip_node_5_add_norm_shift_20 = pip_node_5_add_norm_shiftOh[26];
  assign _zz_pip_node_5_add_norm_shift_21 = pip_node_5_add_norm_shiftOh[27];
  assign _zz_pip_node_5_add_norm_shift_22 = (((((((((((((pip_node_5_add_norm_shiftOh[1] || _zz_pip_node_5_add_norm_shift) || _zz_pip_node_5_add_norm_shift_1) || _zz_pip_node_5_add_norm_shift_3) || _zz_pip_node_5_add_norm_shift_4) || _zz_pip_node_5_add_norm_shift_6) || _zz_pip_node_5_add_norm_shift_8) || _zz_pip_node_5_add_norm_shift_10) || _zz_pip_node_5_add_norm_shift_11) || _zz_pip_node_5_add_norm_shift_13) || _zz_pip_node_5_add_norm_shift_15) || _zz_pip_node_5_add_norm_shift_17) || _zz_pip_node_5_add_norm_shift_19) || _zz_pip_node_5_add_norm_shift_21);
  assign _zz_pip_node_5_add_norm_shift_23 = (((((((((((((pip_node_5_add_norm_shiftOh[2] || _zz_pip_node_5_add_norm_shift) || _zz_pip_node_5_add_norm_shift_2) || _zz_pip_node_5_add_norm_shift_3) || _zz_pip_node_5_add_norm_shift_5) || _zz_pip_node_5_add_norm_shift_6) || _zz_pip_node_5_add_norm_shift_9) || _zz_pip_node_5_add_norm_shift_10) || _zz_pip_node_5_add_norm_shift_12) || _zz_pip_node_5_add_norm_shift_13) || _zz_pip_node_5_add_norm_shift_16) || _zz_pip_node_5_add_norm_shift_17) || _zz_pip_node_5_add_norm_shift_20) || _zz_pip_node_5_add_norm_shift_21);
  assign _zz_pip_node_5_add_norm_shift_24 = (((((((((((pip_node_5_add_norm_shiftOh[4] || _zz_pip_node_5_add_norm_shift_1) || _zz_pip_node_5_add_norm_shift_2) || _zz_pip_node_5_add_norm_shift_3) || _zz_pip_node_5_add_norm_shift_7) || _zz_pip_node_5_add_norm_shift_8) || _zz_pip_node_5_add_norm_shift_9) || _zz_pip_node_5_add_norm_shift_10) || _zz_pip_node_5_add_norm_shift_14) || _zz_pip_node_5_add_norm_shift_15) || _zz_pip_node_5_add_norm_shift_16) || _zz_pip_node_5_add_norm_shift_17);
  assign _zz_pip_node_5_add_norm_shift_25 = (((((((((((pip_node_5_add_norm_shiftOh[8] || _zz_pip_node_5_add_norm_shift_4) || _zz_pip_node_5_add_norm_shift_5) || _zz_pip_node_5_add_norm_shift_6) || _zz_pip_node_5_add_norm_shift_7) || _zz_pip_node_5_add_norm_shift_8) || _zz_pip_node_5_add_norm_shift_9) || _zz_pip_node_5_add_norm_shift_10) || _zz_pip_node_5_add_norm_shift_18) || _zz_pip_node_5_add_norm_shift_19) || _zz_pip_node_5_add_norm_shift_20) || _zz_pip_node_5_add_norm_shift_21);
  assign _zz_pip_node_5_add_norm_shift_26 = (((((((((((pip_node_5_add_norm_shiftOh[16] || _zz_pip_node_5_add_norm_shift_11) || _zz_pip_node_5_add_norm_shift_12) || _zz_pip_node_5_add_norm_shift_13) || _zz_pip_node_5_add_norm_shift_14) || _zz_pip_node_5_add_norm_shift_15) || _zz_pip_node_5_add_norm_shift_16) || _zz_pip_node_5_add_norm_shift_17) || _zz_pip_node_5_add_norm_shift_18) || _zz_pip_node_5_add_norm_shift_19) || _zz_pip_node_5_add_norm_shift_20) || _zz_pip_node_5_add_norm_shift_21);
  assign pip_node_5_add_norm_shift = {_zz_pip_node_5_add_norm_shift_26,{_zz_pip_node_5_add_norm_shift_25,{_zz_pip_node_5_add_norm_shift_24,{_zz_pip_node_5_add_norm_shift_23,_zz_pip_node_5_add_norm_shift_22}}}};
  assign pip_node_5_add_norm_forceInfinity = ((pip_node_5_A_mode == FloatMode_INF) || (pip_node_5_C_mode == FloatMode_INF));
  assign pip_node_5_add_norm_forceZero = ((pip_node_5_add_math_xyMantissa == 28'h0) || ((pip_node_5_A_mode == FloatMode_ZERO) && (pip_node_5_C_mode == FloatMode_ZERO)));
  assign pip_node_5_add_norm_infinityNan = (((pip_node_5_A_mode == FloatMode_INF) && (pip_node_5_C_mode == FloatMode_INF)) && (pip_node_5_A_sign ^ pip_node_5_C_sign));
  assign pip_node_5_add_norm_forceNan = (((pip_node_5_A_mode == FloatMode_NAN) || (pip_node_5_C_mode == FloatMode_NAN)) || pip_node_5_add_norm_infinityNan);
  assign pip_node_5_add_norm_xyMantissaZero = (pip_node_5_add_math_xyMantissa == 28'h0);
  assign pip_node_6_add_result_exponent = _zz_pip_node_6_add_result_exponent;
  assign pip_node_6_add_result_mantissa = (pip_node_6_add_math_xyMantissa <<< pip_node_6_add_norm_shift);
  always @(*) begin
    pip_node_6_add_result_RESULT_sign = pip_node_6_add_shifter_xySign;
    if(!pip_node_6_add_norm_forceNan) begin
      if(!pip_node_6_add_norm_forceInfinity) begin
        if(pip_node_6_add_norm_forceZero) begin
          if(when_FpuAdd_l101) begin
            pip_node_6_add_result_RESULT_sign = (pip_node_6_A_sign && pip_node_6_C_sign);
          end
          if(when_FpuAdd_l104) begin
            pip_node_6_add_result_RESULT_sign = 1'b1;
          end
        end
      end
    end
  end

  assign pip_node_6_add_result_RESULT_mantissa = _zz_pip_node_6_add_result_RESULT_mantissa[27:0];
  assign pip_node_6_add_result_RESULT_exponent = _zz_pip_node_6_add_result_RESULT_exponent;
  always @(*) begin
    pip_node_6_add_result_RESULT_quiet = 1'b0;
    if(pip_node_6_add_norm_forceNan) begin
      pip_node_6_add_result_RESULT_quiet = 1'b1;
    end
  end

  always @(*) begin
    pip_node_6_add_result_RESULT_mode = FloatMode_NORMAL;
    if(pip_node_6_add_norm_forceNan) begin
      pip_node_6_add_result_RESULT_mode = FloatMode_NAN;
    end else begin
      if(pip_node_6_add_norm_forceInfinity) begin
        pip_node_6_add_result_RESULT_mode = FloatMode_INF;
      end else begin
        if(pip_node_6_add_norm_forceZero) begin
          pip_node_6_add_result_RESULT_mode = FloatMode_ZERO;
        end
      end
    end
  end

  assign pip_node_6_add_result_NV = ((pip_node_6_add_norm_infinityNan || ((pip_node_6_A_mode == FloatMode_NAN) && (! pip_node_6_A_quiet))) || ((pip_node_6_C_mode == FloatMode_NAN) && (! pip_node_6_C_quiet)));
  assign when_FpuAdd_l101 = (pip_node_6_add_norm_xyMantissaZero || ((pip_node_6_A_mode == FloatMode_ZERO) && (pip_node_6_C_mode == FloatMode_ZERO)));
  assign when_FpuAdd_l104 = ((pip_node_6_A_sign || pip_node_6_C_sign) && pip_node_6_RD);
  assign s7_value_sign = pip_node_7_add_result_RESULT_sign;
  assign s7_value_exponent = _zz_s7_value_exponent;
  assign _zz_when_AFix_l856 = pip_node_7_add_result_RESULT_mantissa;
  always @(*) begin
    _zz_s7_value_mantissa = _zz_when_AFix_l856[27 : 3];
    if(when_AFix_l856) begin
      _zz_s7_value_mantissa[0] = 1'b1;
    end
  end

  assign when_AFix_l856 = (|_zz_when_AFix_l856[2 : 0]);
  assign s7_value_mantissa = _zz_s7_value_mantissa;
  assign s7_value_mode = pip_node_7_add_result_RESULT_mode;
  assign s7_value_quiet = pip_node_7_add_result_RESULT_quiet;
  assign s7_man = s7_value_mantissa;
  assign s7_incr = (s7_man[1] && (s7_man[0] || s7_man[2]));
  assign s7_rounded = ({1'b0,s7_man[24 : 2]} + _zz_s7_rounded);
  assign s7_exp = ($signed(_zz_s7_exp) + $signed(_zz_s7_exp_2));
  assign s7_packexp = _zz_s7_packexp[7:0];
  always @(*) begin
    io_result = {{s7_value_sign,s7_packexp},s7_rounded[22 : 0]};
    if(when_GeometryFmaProbe_l72) begin
      io_result = {s7_value_sign,31'h0};
      if(pip_node_7_s0_MONLY) begin
        io_result = {pip_node_7_s0_SIGN,31'h0};
      end
    end
  end

  assign when_GeometryFmaProbe_l72 = (s7_value_mode == FloatMode_ZERO);
  assign io_unsupported = (((pip_node_7_BAD || ((s7_value_mode == FloatMode_NORMAL) && (($signed(s7_exp) < $signed(12'hf82)) || ($signed(12'h07f) < $signed(s7_exp))))) || (s7_value_mode == FloatMode_NAN)) || (s7_value_mode == FloatMode_INF));
  assign io_resultValid = pip_node_7_isValid;
  assign pip_node_7_ready = 1'b1;
  assign pip_node_0_ready = pip_node_1_ready;
  assign pip_node_1_ready = pip_node_2_ready;
  assign pip_node_2_ready = pip_node_3_ready;
  assign pip_node_3_ready = pip_node_4_ready;
  assign pip_node_4_ready = pip_node_5_ready;
  assign pip_node_5_ready = pip_node_6_ready;
  assign pip_node_6_ready = pip_node_7_ready;
  assign pip_node_0_isValid = pip_node_0_valid;
  assign pip_node_0_isReady = pip_node_0_ready;
  assign pip_node_1_isValid = pip_node_1_valid;
  assign pip_node_1_isReady = pip_node_1_ready;
  assign pip_node_2_isValid = pip_node_2_valid;
  assign pip_node_2_isReady = pip_node_2_ready;
  assign pip_node_3_isValid = pip_node_3_valid;
  assign pip_node_3_isReady = pip_node_3_ready;
  assign pip_node_4_isValid = pip_node_4_valid;
  assign pip_node_4_isReady = pip_node_4_ready;
  assign pip_node_5_isValid = pip_node_5_valid;
  assign pip_node_5_isReady = pip_node_5_ready;
  assign pip_node_6_isValid = pip_node_6_valid;
  assign pip_node_6_isReady = pip_node_6_ready;
  assign pip_node_7_isValid = pip_node_7_valid;
  always @(posedge clk or posedge reset) begin
    if(reset) begin
      pip_node_1_valid <= 1'b0;
      pip_node_2_valid <= 1'b0;
      pip_node_3_valid <= 1'b0;
      pip_node_4_valid <= 1'b0;
      pip_node_5_valid <= 1'b0;
      pip_node_6_valid <= 1'b0;
      pip_node_7_valid <= 1'b0;
    end else begin
      if(pip_node_0_isReady) begin
        pip_node_1_valid <= pip_node_0_isValid;
      end
      if(pip_node_1_isReady) begin
        pip_node_2_valid <= pip_node_1_isValid;
      end
      if(pip_node_2_isReady) begin
        pip_node_3_valid <= pip_node_2_isValid;
      end
      if(pip_node_3_isReady) begin
        pip_node_4_valid <= pip_node_3_isValid;
      end
      if(pip_node_4_isReady) begin
        pip_node_5_valid <= pip_node_4_isValid;
      end
      if(pip_node_5_isReady) begin
        pip_node_6_valid <= pip_node_5_isValid;
      end
      if(pip_node_6_isReady) begin
        pip_node_7_valid <= pip_node_6_isValid;
      end
    end
  end

  always @(posedge clk) begin
    if(pip_node_0_isReady) begin
      pip_node_1_s0_MUL <= pip_node_0_s0_MUL;
      pip_node_1_s0_EXP <= _zz_pip_node_1_s0_EXP;
      pip_node_1_s0_SIGN <= pip_node_0_s0_SIGN;
      pip_node_1_s0_ZERO <= pip_node_0_s0_ZERO;
      pip_node_1_s0_MONLY <= pip_node_0_s0_MONLY;
      pip_node_1_C_mode <= pip_node_0_C_mode;
      pip_node_1_C_quiet <= pip_node_0_C_quiet;
      pip_node_1_C_sign <= pip_node_0_C_sign;
      pip_node_1_C_exponent <= _zz_pip_node_1_C_exponent;
      pip_node_1_C_mantissa <= pip_node_0_C_mantissa;
      pip_node_1_BAD <= pip_node_0_BAD;
      pip_node_1_RD <= pip_node_0_RD;
    end
    if(pip_node_1_isReady) begin
      pip_node_2_s0_SIGN <= pip_node_1_s0_SIGN;
      pip_node_2_s0_MONLY <= pip_node_1_s0_MONLY;
      pip_node_2_C_mode <= pip_node_1_C_mode;
      pip_node_2_C_quiet <= pip_node_1_C_quiet;
      pip_node_2_C_sign <= pip_node_1_C_sign;
      pip_node_2_C_exponent <= _zz_pip_node_2_C_exponent;
      pip_node_2_C_mantissa <= pip_node_1_C_mantissa;
      pip_node_2_BAD <= pip_node_1_BAD;
      pip_node_2_RD <= pip_node_1_RD;
      pip_node_2_A_mode <= pip_node_1_A_mode;
      pip_node_2_A_quiet <= pip_node_1_A_quiet;
      pip_node_2_A_sign <= pip_node_1_A_sign;
      pip_node_2_A_exponent <= _zz_pip_node_2_A_exponent;
      pip_node_2_A_mantissa <= pip_node_1_A_mantissa;
    end
    if(pip_node_2_isReady) begin
      pip_node_3_s0_SIGN <= pip_node_2_s0_SIGN;
      pip_node_3_s0_MONLY <= pip_node_2_s0_MONLY;
      pip_node_3_C_mode <= pip_node_2_C_mode;
      pip_node_3_C_quiet <= pip_node_2_C_quiet;
      pip_node_3_C_sign <= pip_node_2_C_sign;
      pip_node_3_C_exponent <= _zz_pip_node_3_C_exponent;
      pip_node_3_C_mantissa <= pip_node_2_C_mantissa;
      pip_node_3_BAD <= pip_node_2_BAD;
      pip_node_3_RD <= pip_node_2_RD;
      pip_node_3_A_mode <= pip_node_2_A_mode;
      pip_node_3_A_quiet <= pip_node_2_A_quiet;
      pip_node_3_A_sign <= pip_node_2_A_sign;
      pip_node_3_A_exponent <= _zz_pip_node_3_A_exponent;
      pip_node_3_A_mantissa <= pip_node_2_A_mantissa;
      pip_node_3_add_preShift_absRs1Bigger <= pip_node_2_add_preShift_absRs1Bigger;
      pip_node_3_add_preShift_needSub <= pip_node_2_add_preShift_needSub;
      pip_node_3_add_preShift_passThrough <= pip_node_2_add_preShift_passThrough;
      pip_node_3_add_preShift_expDifAbsSat <= pip_node_2_add_preShift_expDifAbsSat;
    end
    if(pip_node_3_isReady) begin
      pip_node_4_s0_SIGN <= pip_node_3_s0_SIGN;
      pip_node_4_s0_MONLY <= pip_node_3_s0_MONLY;
      pip_node_4_C_mode <= pip_node_3_C_mode;
      pip_node_4_C_quiet <= pip_node_3_C_quiet;
      pip_node_4_C_sign <= pip_node_3_C_sign;
      pip_node_4_C_exponent <= _zz_pip_node_4_C_exponent;
      pip_node_4_C_mantissa <= pip_node_3_C_mantissa;
      pip_node_4_BAD <= pip_node_3_BAD;
      pip_node_4_RD <= pip_node_3_RD;
      pip_node_4_A_mode <= pip_node_3_A_mode;
      pip_node_4_A_quiet <= pip_node_3_A_quiet;
      pip_node_4_A_sign <= pip_node_3_A_sign;
      pip_node_4_A_exponent <= _zz_pip_node_4_A_exponent;
      pip_node_4_A_mantissa <= pip_node_3_A_mantissa;
      pip_node_4_add_preShift_needSub <= pip_node_3_add_preShift_needSub;
      pip_node_4_add_preShift_passThrough <= pip_node_3_add_preShift_passThrough;
      pip_node_4_add_shifter_xySign <= pip_node_3_add_shifter_xySign;
      pip_node_4_add_shifter_xMantissa <= pip_node_3_add_shifter_xMantissa;
      pip_node_4_add_shifter_shifter <= pip_node_3_add_shifter_shifter;
      pip_node_4_add_shifter_yMantissa <= pip_node_3_add_shifter_yMantissa;
      pip_node_4_add_shifter_xyExponent <= _zz_pip_node_4_add_shifter_xyExponent;
    end
    if(pip_node_4_isReady) begin
      pip_node_5_s0_SIGN <= pip_node_4_s0_SIGN;
      pip_node_5_s0_MONLY <= pip_node_4_s0_MONLY;
      pip_node_5_C_mode <= pip_node_4_C_mode;
      pip_node_5_C_quiet <= pip_node_4_C_quiet;
      pip_node_5_C_sign <= pip_node_4_C_sign;
      pip_node_5_C_exponent <= _zz_pip_node_5_C_exponent;
      pip_node_5_C_mantissa <= pip_node_4_C_mantissa;
      pip_node_5_BAD <= pip_node_4_BAD;
      pip_node_5_RD <= pip_node_4_RD;
      pip_node_5_A_mode <= pip_node_4_A_mode;
      pip_node_5_A_quiet <= pip_node_4_A_quiet;
      pip_node_5_A_sign <= pip_node_4_A_sign;
      pip_node_5_A_exponent <= _zz_pip_node_5_A_exponent;
      pip_node_5_A_mantissa <= pip_node_4_A_mantissa;
      pip_node_5_add_shifter_xySign <= pip_node_4_add_shifter_xySign;
      pip_node_5_add_shifter_xyExponent <= _zz_pip_node_5_add_shifter_xyExponent;
      pip_node_5_add_math_roundingScrap <= pip_node_4_add_math_roundingScrap;
      pip_node_5_add_math_xyMantissa <= pip_node_4_add_math_xyMantissa;
    end
    if(pip_node_5_isReady) begin
      pip_node_6_s0_SIGN <= pip_node_5_s0_SIGN;
      pip_node_6_s0_MONLY <= pip_node_5_s0_MONLY;
      pip_node_6_C_mode <= pip_node_5_C_mode;
      pip_node_6_C_quiet <= pip_node_5_C_quiet;
      pip_node_6_C_sign <= pip_node_5_C_sign;
      pip_node_6_C_exponent <= _zz_pip_node_6_C_exponent;
      pip_node_6_C_mantissa <= pip_node_5_C_mantissa;
      pip_node_6_BAD <= pip_node_5_BAD;
      pip_node_6_RD <= pip_node_5_RD;
      pip_node_6_A_mode <= pip_node_5_A_mode;
      pip_node_6_A_quiet <= pip_node_5_A_quiet;
      pip_node_6_A_sign <= pip_node_5_A_sign;
      pip_node_6_A_exponent <= _zz_pip_node_6_A_exponent;
      pip_node_6_A_mantissa <= pip_node_5_A_mantissa;
      pip_node_6_add_shifter_xySign <= pip_node_5_add_shifter_xySign;
      pip_node_6_add_shifter_xyExponent <= _zz_pip_node_6_add_shifter_xyExponent;
      pip_node_6_add_math_roundingScrap <= pip_node_5_add_math_roundingScrap;
      pip_node_6_add_math_xyMantissa <= pip_node_5_add_math_xyMantissa;
      pip_node_6_add_norm_shift <= pip_node_5_add_norm_shift;
      pip_node_6_add_norm_forceInfinity <= pip_node_5_add_norm_forceInfinity;
      pip_node_6_add_norm_forceZero <= pip_node_5_add_norm_forceZero;
      pip_node_6_add_norm_infinityNan <= pip_node_5_add_norm_infinityNan;
      pip_node_6_add_norm_forceNan <= pip_node_5_add_norm_forceNan;
      pip_node_6_add_norm_xyMantissaZero <= pip_node_5_add_norm_xyMantissaZero;
    end
    if(pip_node_6_isReady) begin
      pip_node_7_s0_SIGN <= pip_node_6_s0_SIGN;
      pip_node_7_s0_MONLY <= pip_node_6_s0_MONLY;
      pip_node_7_BAD <= pip_node_6_BAD;
      pip_node_7_add_result_RESULT_mode <= pip_node_6_add_result_RESULT_mode;
      pip_node_7_add_result_RESULT_quiet <= pip_node_6_add_result_RESULT_quiet;
      pip_node_7_add_result_RESULT_sign <= pip_node_6_add_result_RESULT_sign;
      pip_node_7_add_result_RESULT_exponent <= _zz_pip_node_7_add_result_RESULT_exponent;
      pip_node_7_add_result_RESULT_mantissa <= pip_node_6_add_result_RESULT_mantissa;
    end
  end


endmodule
