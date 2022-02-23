import inspect
import typing
from typing import Type, Any, Dict
from dataclasses import dataclass
from xdsl.ir import Operation, ParametrizedAttribute


def new_op(op_name: str, num_results: int, num_operands: int,
           num_regions: int) -> Type[Operation]:

    @dataclass(eq=False)
    class OpBase(Operation):
        name: str = op_name

        def verify_(self) -> None:
            if len(self.results) != num_results or len(
                    self.operands) != num_operands or len(
                        self.regions) != num_regions:
                raise Exception("%s verifier" % op_name)

    return OpBase


def new_type(type_name: str):

    @dataclass(frozen=True, eq=False)
    class TypeBase(ParametrizedAttribute):
        name: str = type_name

        def verify_(self: ParametrizedAttribute) -> None:
            if len(self.parameters) != 0:
                raise Exception(f"{type_name} should have no parameters")

    return TypeBase


def is_satisfying_hint(arg: Any, hint: Any) -> bool:
    """
    Check if `arg` is of the type described by `hint`.
    For now, only lists, dictionaries, unions, and non-generic
    classes are supported for type hints.
    """
    if inspect.isclass(hint):
        return isinstance(arg, hint)

    if typing.get_origin(hint) == list:
        if not isinstance(arg, list):
            return False
        if not arg:
            return True
        return is_satisfying_hint(arg[0], typing.get_args(hint)[0])

    if typing.get_origin(hint) == dict:
        if not isinstance(arg, dict):
            return False
        if not arg:
            return True
        arg_dict: Dict[Any, Any] = arg
        return is_satisfying_hint(next(iter(arg_dict)),
                                  typing.get_args(hint)[0])

    if typing.get_origin(hint) == typing.Union:
        for union_arg in typing.get_args(hint):
            if is_satisfying_hint(arg, union_arg):
                return True
        return False

    raise ValueError(f"is_satisfying_hint: unsupported type hint '{hint}'")
