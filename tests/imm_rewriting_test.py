from __future__ import annotations
from io import StringIO
import xdsl.dialects.arith as arith
import xdsl.dialects.scf as scf
from xdsl.parser import Parser
from xdsl.printer import Printer
from xdsl.dialects.func import *
from xdsl.elevate import *
from xdsl.immutable_ir import *
import difflib


def apply_strategy_and_compare(program: str, expected_program: str,
                               strategy: Strategy):
    ctx = MLContext()
    Builtin(ctx)
    Func(ctx)
    Arith(ctx)
    scf.Scf(ctx)

    parser = Parser(ctx, program)
    module: Operation = parser.parse_op()
    imm_module: IOp = get_immutable_copy(module)

    rr = strategy.apply(imm_module)
    assert rr.isSuccess()

    # for debugging
    printer = Printer()
    print(f'Result after applying "{strategy}":')
    printer.print_op(rr.result_op.get_mutable_copy())
    print()

    file = StringIO("")
    printer = Printer(stream=file)
    printer.print_op(rr.result_op.get_mutable_copy())

    diff = difflib.Differ().compare(file.getvalue().splitlines(True),
                                    expected_program.splitlines(True))
    if file.getvalue().strip() != expected_program.strip():
        print("Did not get expected output! Diff:")
        print(''.join(diff))
        assert False


@dataclass
class CommuteAdd(Strategy):

    def impl(self, op: IOp) -> RewriteResult:
        match op:
            case IOp(op_type=arith.Addi,
                     operands=[operand0, operand1]):
                result = from_op(op, operands=[operand1, operand0])
                return success(result, op)
            case _:
                return failure(self)


@dataclass
class FoldConstantAdd(Strategy):

    def impl(self, op: IOp) -> RewriteResult:
        match op:
          case IOp(op_type=arith.Addi,
                  operands=[ISSAValue(op=IOp(op_type=arith.Constant, 
                                             attributes={"value": IntegerAttr() as attr1}) as c1), 
                                  ISSAValue(op=IOp(op_type=arith.Constant, 
                                             attributes={"value": IntegerAttr() as attr2}))]):
            result = from_op(c1,
                      attributes={
                          "value":
                          IntegerAttr.from_params(
                              attr1.value.data + attr2.value.data,
                              attr1.typ)
                      })
            return success(result, op)
          case _:
            return failure(self)


@dataclass
class ChangeConstantTo42(Strategy):

    def impl(self, op: IOp) -> RewriteResult:
        match op:
          case IOp(op_type=arith.Constant, 
                    attributes={"value": IntegerAttr(typ=IntegerType(width=IntAttr(data=32))) as attr}):
              result = from_op(op,
                        attributes={
                            "value":
                            IntegerAttr.from_params(42, attr.typ)
                        })
              return success(result, op)
          case _:
              return failure(self)


@dataclass
class InlineIf(Strategy):

    def impl(self, op: IOp) -> RewriteResult:
        match op:
            case IOp(op_type=scf.If,
                        operands=[IResult(op=IOp(op_type=arith.Constant, attributes={"value": IntegerAttr(value=IntAttr(data=1))}))],
                        region=IRegion(ops=[*_, IOp(op_type=scf.Yield, operands=[IResult(op=returned_op)])])):                         
                        return success(returned_op, op)
            case _:
                return failure(self)

@dataclass
class AddZero(Strategy):

    def impl(self, op: IOp) -> RewriteResult:
        match op:
            case IOp(results=[IResult(typ=IntegerType() as type)]):
                result = new_op(Addi, [
                    op,
                    new_op(Constant,
                        attributes={
                            "value": IntegerAttr.from_int_and_width(0, 32)
                        }, result_types=[type])
                ], result_types=[type])
                return success(result, op)
            case _:
                return failure(self)

def test_double_commute():
    """Tests a strategy which swaps the two operands of an arith.addi."""

    before = \
"""module() {
  %0 : !i32 = arith.constant() ["value" = 4 : !i32]
  %1 : !i32 = arith.constant() ["value" = 2 : !i32]
  %2 : !i32 = arith.constant() ["value" = 1 : !i32]
  %3 : !i32 = arith.addi(%2 : !i32, %1 : !i32)
  %4 : !i32 = arith.addi(%3 : !i32, %0 : !i32)
  func.return(%4 : !i32)
}
"""
    once_commuted = \
"""module() {
  %0 : !i32 = arith.constant() ["value" = 4 : !i32]
  %1 : !i32 = arith.constant() ["value" = 2 : !i32]
  %2 : !i32 = arith.constant() ["value" = 1 : !i32]
  %3 : !i32 = arith.addi(%2 : !i32, %1 : !i32)
  %4 : !i32 = arith.addi(%0 : !i32, %3 : !i32)
  func.return(%4 : !i32)
}
"""
    apply_strategy_and_compare(program=before,
                               expected_program=once_commuted,
                               strategy=topdown(CommuteAdd()))
    apply_strategy_and_compare(program=once_commuted,
                               expected_program=before,
                               strategy=topdown(CommuteAdd()))             
    apply_strategy_and_compare(program=before,
                               expected_program=before,
                               strategy=seq(topdown(CommuteAdd()),
                                            topdown(CommuteAdd())))


def test_commute_block_args():
    """Tests a strategy which swaps the two operands of 
    an arith.addi which are block_args."""

    before = \
"""module() {
  func.func() ["sym_name" = "test", "type" = !fun<[!i32, !i32], [!i32]>, "sym_visibility" = "private"] {
  ^0(%0 : !i32, %1 : !i32):
    %2 : !i32 = arith.addi(%0 : !i32, %1 : !i32)
    func.return(%2 : !i32)
  }
}
"""
    commuted = \
"""module() {
  func.func() ["sym_name" = "test", "type" = !fun<[!i32, !i32], [!i32]>, "sym_visibility" = "private"] {
  ^0(%0 : !i32, %1 : !i32):
    %2 : !i32 = arith.addi(%1 : !i32, %0 : !i32)
    func.return(%2 : !i32)
  }
}
"""
    apply_strategy_and_compare(program=before,
                               expected_program=commuted,
                               strategy=topdown(CommuteAdd()))
    apply_strategy_and_compare(program=commuted,
                               expected_program=before,
                               strategy=topdown(CommuteAdd()))
    apply_strategy_and_compare(program=before,
                               expected_program=before,
                               strategy=seq(
                                        topdown(CommuteAdd()),
                                        topdown(CommuteAdd())))


def test_rewriting_with_blocks():
    before = \
"""module() {
  func.func() ["sym_name" = "test", "type" = !fun<[!i32], [!i32]>, "sym_visibility" = "private"] {
  ^0(%0 : !i1):
    %1 : !i32 = scf.if(%0 : !i1) {
      %2 : !i32 = arith.constant() ["value" = 0 : !i32]
      scf.yield(%2 : !i32)
    }
    func.return(%1 : !i32)
  }
}
"""
    constant_changed = \
"""module() {
  func.func() ["sym_name" = "test", "type" = !fun<[!i32], [!i32]>, "sym_visibility" = "private"] {
  ^0(%0 : !i1):
    %1 : !i32 = scf.if(%0 : !i1) {
      %2 : !i32 = arith.constant() ["value" = 42 : !i32]
      scf.yield(%2 : !i32)
    }
    func.return(%1 : !i32)
  }
}
"""

    apply_strategy_and_compare(program=before,
                               expected_program=constant_changed,
                               strategy=topdown(ChangeConstantTo42()))


def test_constant_folding():
    before = \
"""module() {
%0 : !i32 = arith.constant() ["value" = 1 : !i32]
%1 : !i32 = arith.constant() ["value" = 2 : !i32]
%2 : !i32 = arith.addi(%0 : !i32, %1 : !i32)
%3 : !i32 = arith.constant() ["value" = 4 : !i32]
%4 : !i32 = arith.addi(%2 : !i32, %3 : !i32)
func.return(%4 : !i32)
}
"""
    once_folded = \
"""module() {
  %0 : !i32 = arith.constant() ["value" = 1 : !i32]
  %1 : !i32 = arith.constant() ["value" = 2 : !i32]
  %2 : !i32 = arith.constant() ["value" = 3 : !i32]
  %3 : !i32 = arith.constant() ["value" = 4 : !i32]
  %4 : !i32 = arith.addi(%2 : !i32, %3 : !i32)
  func.return(%4 : !i32)
}
"""
    twice_folded = \
"""module() {
  %0 : !i32 = arith.constant() ["value" = 1 : !i32]
  %1 : !i32 = arith.constant() ["value" = 2 : !i32]
  %2 : !i32 = arith.constant() ["value" = 3 : !i32]
  %3 : !i32 = arith.constant() ["value" = 4 : !i32]
  %4 : !i32 = arith.constant() ["value" = 7 : !i32]
  func.return(%4 : !i32)
}
"""

    apply_strategy_and_compare(program=before,
                               expected_program=once_folded,
                               strategy=topdown(FoldConstantAdd()))

    apply_strategy_and_compare(program=once_folded,
                               expected_program=twice_folded,
                               strategy=topdown(FoldConstantAdd()))
                                            
    apply_strategy_and_compare(program=before,
                               expected_program=twice_folded,
                               strategy=seq(topdown(FoldConstantAdd()),
                                            topdown(FoldConstantAdd())))

def test_inline_if():
    before = \
"""module() {
  func.func() ["sym_name" = "test", "type" = !fun<[], [!i32]>, "sym_visibility" = "private"] {
  ^0():
    %0 : !i1 = arith.constant() ["value" = 1 : !i1]
    %1 : !i32 = scf.if(%0 : !i1) {
      %2 : !i32 = arith.constant() ["value" = 42 : !i32]
      scf.yield(%2 : !i32)
    }
    func.return(%1 : !i32)
  }
}
"""
    inlined = \
"""module() {
  func.func() ["sym_name" = "test", "type" = !fun<[], [!i32]>, "sym_visibility" = "private"] {
    %0 : !i1 = arith.constant() ["value" = 1 : !i1]
    %1 : !i32 = arith.constant() ["value" = 42 : !i32]
    func.return(%1 : !i32)
  }
}
"""

    apply_strategy_and_compare(program=before,
                               expected_program=inlined,
                               strategy=topdown(InlineIf()))

def test_inline_and_fold():
    before = \
"""module() {
  func.func() ["sym_name" = "test", "type" = !fun<[], [!i32]>, "sym_visibility" = "private"] {
  ^0():
    %0 : !i1 = arith.constant() ["value" = 1 : !i1]
    %1 : !i32 = scf.if(%0 : !i1) {
      %2 : !i32 = arith.constant() ["value" = 1 : !i32]
      %3 : !i32 = arith.constant() ["value" = 2 : !i32]
      %4 : !i32 = arith.addi(%2 : !i32, %3 : !i32)
      %5 : !i32 = arith.constant() ["value" = 4 : !i32]
      %6 : !i32 = arith.addi(%4 : !i32, %5 : !i32)
      scf.yield(%6 : !i32)
    }
    func.return(%1 : !i32)
  }
}
"""
    folded_and_inlined = \
"""module() {
  func.func() ["sym_name" = "test", "type" = !fun<[], [!i32]>, "sym_visibility" = "private"] {
    %0 : !i1 = arith.constant() ["value" = 1 : !i1]
    %1 : !i32 = arith.constant() ["value" = 7 : !i32]
    func.return(%1 : !i32)
  }
}
"""
    apply_strategy_and_compare(program=before,
                               expected_program=folded_and_inlined,
                               strategy=seq(topdown(FoldConstantAdd()), 
                                            seq(topdown(FoldConstantAdd()), 
                                                topdown(InlineIf()))))
    apply_strategy_and_compare(program=before,
                               expected_program=folded_and_inlined,
                               strategy=seq(topdown(InlineIf()), 
                                            seq(topdown(FoldConstantAdd()), 
                                                topdown(FoldConstantAdd()))))
    apply_strategy_and_compare(program=before,
                               expected_program=folded_and_inlined,
                               strategy=seq(topdown(FoldConstantAdd()), 
                                            seq(topdown(InlineIf()), 
                                                topdown(FoldConstantAdd()))))

def test_add_zero():
    before = \
"""module() {
%0 : !i32 = arith.constant() ["value" = 1 : !i32]
func.return(%0 : !i32)
}
"""
    added_zero = \
"""module() {
  %0 : !i32 = arith.constant() ["value" = 1 : !i32]
  %1 : !i32 = arith.constant() ["value" = 0 : !i32]
  %2 : !i32 = arith.addi(%0 : !i32, %1 : !i32)
  func.return(%2 : !i32)
}
"""
    apply_strategy_and_compare(program=before,
                              expected_program=added_zero,
                              strategy=topdown(AddZero()))

def test_deeper_nested_block_args_commute():
    """This test demonstrates that substitution of block arguments also works when the 
    concerned op is at a deeper nesting level than the block supplying the block args.
    """
  
    before = \
"""module() {
  func.func() ["sym_name" = "test", "type" = !fun<[!i32, !i32], [!i32]>, "sym_visibility" = "private"] {
  ^0(%0 : !i32, %1 : !i32):
    %2 : !i1 = arith.constant() ["value" = 1 : !i1]
    %3 : !i32 = scf.if(%2 : !i1) {
      %4 : !i32 = arith.addi(%0 : !i32, %1 : !i32)
      scf.yield(%4 : !i32)
    }
    func.return(%3 : !i32)
  }
}
"""
    nested_commute = \
"""module() {
  func.func() ["sym_name" = "test", "type" = !fun<[!i32, !i32], [!i32]>, "sym_visibility" = "private"] {
  ^0(%0 : !i32, %1 : !i32):
    %2 : !i1 = arith.constant() ["value" = 1 : !i1]
    %3 : !i32 = scf.if(%2 : !i1) {
      %4 : !i32 = arith.addi(%1 : !i32, %0 : !i32)
      scf.yield(%4 : !i32)
    }
    func.return(%3 : !i32)
  }
}
"""
    apply_strategy_and_compare(program=before,
                              expected_program=nested_commute,
                              strategy=topdown(CommuteAdd()))


if __name__ == "__main__":
    test_double_commute()
    test_commute_block_args()
    test_rewriting_with_blocks()
    # TODO: rewriting with successors
    test_constant_folding()
    test_inline_if()
    test_inline_and_fold()
    test_add_zero()
    test_deeper_nested_block_args_commute()