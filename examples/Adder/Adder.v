// 128-bit combinational full adder, shared by both simulator examples.
module Adder (
    input [127:0] a,
    input [127:0] b,
    input cin,
    output [127:0] sum,
    output cout
);
    assign {cout, sum} = a + b + cin;
endmodule
