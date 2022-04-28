from __future__ import annotations
from optparse import Option
from xdsl.dialects.builtin import *
from xdsl.dialects.arith import *
from xdsl.rewriter import Rewriter
from xdsl.printer import Printer


@dataclass(frozen=True)
class ImmutableSSAValue:
    typ: Attribute

    def get_op(self) -> ImmutableOperation:
        if isinstance(self, ImmutableOpResult):
            return self.op
        return None  # type: ignore

    def __hash__(self):
        return hash(self.typ)

@dataclass(frozen=True)
class ImmutableOpResult(ImmutableSSAValue):
    op: ImmutableOperation
    result_index: int

    def __hash__(self):
        return hash((self.op, self.result_index))

@dataclass(frozen=True)
class ImmutableBlockArgument(ImmutableSSAValue):
    block: ImmutableBlock
    index: int

    def __hash__(self):
        return hash((self.block, self.index))

@dataclass(frozen=True)
class ImmutableRegion:
    blocks: FrozenList[ImmutableBlock]
    # parent_op: Optional[ImmutableOperation] = None

    def __hash__(self):
        return hash((self.blocks))

    def __post_init__(self):
        # for block in self.blocks:
        #     block.parent_region = self
        self.blocks.freeze()

    @property
    def block(self):
        return self.blocks[0]

    def get_mutable_copy(self, value_mapping: Optional[Dict[ImmutableSSAValue, SSAValue]] = None, block_mapping: Optional[Dict[ImmutableBlock, Block]] = None) -> Region:
        if value_mapping is None:
            value_mapping = {}
        if block_mapping is None:
            block_mapping = {}
        mutable_blocks: List[Block] = []
        for block in self.blocks:
            mutable_blocks.append(block.get_mutable_copy(value_mapping=value_mapping, block_mapping=block_mapping))
        return Region.from_block_list(mutable_blocks)

    @classmethod
    def _create_new(
            cls,
            arg: ImmutableRegion | List[ImmutableBlock]
        | List[ImmutableOperation],
            value_map: Optional[Dict] = None,
            block_map: Optional[Dict] = None
    ) -> Tuple[ImmutableRegion, Region]:
        """Creates a new mutable region and returns an immutable view on it and the region."""
        if value_map is None:
            value_map = {}
        if block_map is None:
            block_map = {}

        match arg:
            case ImmutableRegion():
                blocks = list(arg.blocks)
            case [*blocks_] if all(
                isinstance(block, ImmutableBlock) for block in blocks_):
                blocks = blocks_
            case [*ops_
                  ] if all(isinstance(op, ImmutableOperation) for op in ops_):
                raise Exception(
                    "Creating an ImmutableRegion from ops directly is not yet implemented"
                )
                blocks = []
            case _:
                raise Exception(
                    "unsupported argument to create ImmutableRegion from.")

        mutable_blocks = []
        immutable_blocks = []
        for immutable_block in blocks:
            if immutable_block.parent_region is not None:  # type: ignore
                # if the immutable_block already has a parent_region we have to recreate it
                new_block = ImmutableBlock._create_new(
                    immutable_block, value_map, block_map)  # type: ignore
                immutable_blocks.append(new_block[0])
                mutable_blocks.append(new_block[1])
            else:
                immutable_blocks.append(immutable_block)
                mutable_blocks.append(immutable_block._block)  # type: ignore

        new_region = Region.get(mutable_blocks)
        return ImmutableRegion.from_block_list(new_region.blocks), new_region

    @classmethod
    def create_new(
        cls,
        arg: ImmutableRegion | List[ImmutableBlock] | List[ImmutableOperation]
    ) -> ImmutableRegion:
        """Creates a new mutable region and returns an immutable view on it."""
        return cls._create_new(arg)[0]

    # @staticmethod
    # def from_immutable_operation_list(
    #         ops: List[ImmutableOperation]) -> ImmutableRegion:
    #     block = ImmutableBlock.from_immutable_ops(ops)
    #     return ImmutableRegion(FrozenList([block]))

    # @staticmethod
    # def from_operation_list(ops: List[Operation]) -> ImmutableRegion:
    #     block = ImmutableBlock.from_ops(ops)
    #     return ImmutableRegion(FrozenList([block]))

    # @staticmethod
    # def from_immutable_block_list(
    #         blocks: List[ImmutableBlock]) -> ImmutableRegion:
    #     return ImmutableRegion(FrozenList(blocks))

    @staticmethod
    def from_block_list(blocks: List[Block]) -> ImmutableRegion:
        immutable_blocks = [
            ImmutableBlock.from_block(block) for block in blocks
        ]
        assert (blocks[0].parent is not None)
        return ImmutableRegion(FrozenList(immutable_blocks))

    def walk(self, fun: Callable[[ImmutableOperation], None]) -> None:
        for block in self.blocks:
            block.walk(fun)


@dataclass(frozen=True)
class ImmutableBlock:
    args: FrozenList[ImmutableBlockArgument]
    ops: FrozenList[ImmutableOperation]

    def __hash__(self):
        return hash((self.args, self.ops))

    def __post_init__(self):
        for arg in self.args:
            object.__setattr__(arg, "block", self)

        self.args.freeze()
        self.ops.freeze()

    def get_mutable_copy(self, value_mapping: Optional[Dict[ImmutableSSAValue, SSAValue]] = None, block_mapping: Optional[Dict[ImmutableBlock, Block]] = None) -> Block:
        if value_mapping is None:
            value_mapping = {}
        if block_mapping is None:
            block_mapping = {}

        new_block = Block.from_arg_types([arg.typ for arg in self.args])
        for idx, arg in enumerate(self.args):
            value_mapping[arg] = new_block.args[idx]
        block_mapping[self] = new_block

        for immutable_op in self.ops:
            new_block.add_op(immutable_op.get_mutable_copy(value_mapping=value_mapping, block_mapping=block_mapping))
        return new_block

    @classmethod
    def _create_new(
            cls,
            arg: ImmutableBlock | List[ImmutableOperation],
            old_block: Optional[ImmutableBlock] = None,
            value_map: Optional[Dict] = None,
            block_map: Optional[Dict] = None) -> Tuple[ImmutableBlock, Block]:
        """Creates a new mutable block and returns an immutable view on it and the mutable block itself."""

        if value_map is None:
            value_map = {}
        if block_map is None:
            block_map = {}

        args = old_block.args if old_block is not None else []

        match arg:
            case ImmutableBlock():
                if old_block is None:
                    args.extend(list(arg.args))
                ops = list(arg.ops)
                new_block = Block.from_arg_types(
                    [block_arg.typ for block_arg in args])

                for idx, old_block_arg in enumerate(arg.args):
                    value_map[arg._block.args[idx]] = new_block.args[idx]
                block_map[arg._block] = new_block
            case [*operations] if all(
                isinstance(op, ImmutableOperation) for op in operations):
                ops = operations
                new_block = Block.from_arg_types(
                    [block_arg.typ for block_arg in args])
            case _:
                raise Exception(
                    "unsupported argument to create ImmutableBlock from.")

        immutable_ops = []
        if len(ops) == 0:
            return ImmutableBlock.from_block(new_block), new_block

        if (immutable_op := ops[-1]).parent_block is not None:
            # if the immutable_op already has a parent_block we have to recreate it
            new_ops = ImmutableOperation._create_new(
                immutable_op._op.__class__, list(immutable_op.operands),
                immutable_op.result_types, immutable_op.get_attributes_copy(),
                list(immutable_op.successors), list(immutable_op.regions),
                value_map, block_map)

            immutable_ops.extend(new_ops[0])
            new_block.add_ops(new_ops[1])
        else:
            # TODO: problem if new operations are mixed with old operations?
            # I am not sure whether this even comes up? It shouldn't I think
            immutable_ops.extend(ops)
            new_block.add_ops([imm_op._op for imm_op in ops])

        # here we have to acutally replace the old blockArgs with the new ones
        # in the mutable Block.
        # TODO: brute force solution for now
        for op in new_block.ops:
            for old_imm_block_arg in args:
                if (old_block_arg :=
                    old_imm_block_arg.get_mutable()) in op.operands:
                    index = op.operands.index(old_block_arg)
                    op.replace_operand(index,
                                       new_block.args[old_block_arg.index])

        # This rebuilds the ImmutableOperations we already have, but that is required currently:
        # The ImmutableOperations might need updated references to BlockArgs.
        # return ImmutableBlock.from_block(new_block), new_block

        # Get new immutableBlockArgs:
        immutable_args: List[ImmutableBlockArgument] = []
        for idx, old_imm_block_arg in enumerate(args):
            if old_imm_block_arg.get_mutable() in value_map:
                immutable_args.append(
                    value_map[old_imm_block_arg.get_mutable()])
            else:
                immutable_args.append(
                    ImmutableBlockArgument(old_imm_block_arg.typ, None,
                                           old_imm_block_arg.index))
            value_map[new_block.args[idx]] = immutable_args[-1]

        return ImmutableBlock(new_block, FrozenList(immutable_args),
                              FrozenList(immutable_ops)), new_block

    @classmethod
    def create_new(
            cls,
            arg: Union[ImmutableBlock, List[ImmutableOperation]],
            old_block: Optional[ImmutableBlock] = None) -> ImmutableBlock:
        """Creates a new mutable block and returns an immutable view on it."""
        return cls._create_new(arg, old_block)[0]

    @staticmethod
    def from_block(block: Block) -> ImmutableBlock:
        value_map: dict[SSAValue, ImmutableSSAValue] = {}
        block_map: dict[Block, ImmutableBlock] = {}

        args: List[ImmutableBlockArgument] = []
        for arg in block.args:
            immutable_arg = ImmutableBlockArgument(arg.typ, None, arg.index)
            args.append(immutable_arg)
            value_map[arg] = immutable_arg

        immutable_ops = [
            ImmutableOperation.from_op(op,
                                       value_map=value_map,
                                       block_map=block_map) for op in block.ops
        ]

        return ImmutableBlock(FrozenList(args),
                              FrozenList(immutable_ops))

    @staticmethod
    def _from_args(block: Block, block_args: List[ImmutableBlockArgument],
                   imm_ops: List[ImmutableOperation]) -> ImmutableBlock:
        return ImmutableBlock(FrozenList(block_args),
                              FrozenList(imm_ops))

    # @staticmethod
    # def from_immutable_ops(ops: List[ImmutableOperation]) -> ImmutableBlock:
    #     return ImmutableBlock(FrozenList([]), FrozenList(ops))

    # @staticmethod
    # def from_ops(ops: List[Operation]) -> ImmutableBlock:
    #     context: dict[Operation, ImmutableOperation] = {}
    #     immutable_ops = [ImmutableOperation.from_op(op, context) for op in ops]
    #     return ImmutableBlock.from_immutable_ops(immutable_ops)

    def walk(self, fun: Callable[[ImmutableOperation], None]) -> None:
        for op in self.ops:
            op.walk(fun)


def get_immutable_copy(op: Operation) -> ImmutableOperation:
    return ImmutableOperation.from_op(op, {})


@dataclass(frozen=True)
class ImmutableOperation:
    name: str
    # _op: Operation
    op_type: type[Operation]
    operands: FrozenList[ImmutableSSAValue]
    results: FrozenList[ImmutableOpResult]
    attributes: Dict[str, Attribute]
    successors: FrozenList[ImmutableBlock]
    regions: FrozenList[ImmutableRegion]
    parent_block: Optional[ImmutableBlock] = None

    def __hash__(self) -> int:
        return hash((self.name, self.attributes.values, self.regions))

    @property
    def region(self):
        return self.regions[0]

    @property
    def result_types(self) -> List[Attribute]:
        return [result.typ for result in self.results]

    def __post_init__(self):
        for result in self.results:
            object.__setattr__(result, "op", self)
        self.operands.freeze()
        self.results.freeze()
        self.successors.freeze()
        self.regions.freeze()

    def get_mutable_copy(self, value_mapping: Optional[Dict[ImmutableSSAValue, SSAValue]] = None, block_mapping: Optional[Dict[ImmutableBlock, Block]] = None) -> Operation:
        if value_mapping is None:
            value_mapping = {}
        if block_mapping is None:
            block_mapping = {}

        mutable_operands: List[SSAValue] = []
        for operand in self.operands:
            if operand in value_mapping:
                mutable_operands.append(value_mapping[operand])
            else:
                raise Exception("SSAValue used before definition")                  
                
        mutable_successors: List[Block] = []
        for successor in self.successors:
            if successor in block_mapping:
                mutable_successors.append(block_mapping[successor])
            else:
                raise Exception("Block used before definition")

        mutable_regions: List[Region] = []
        for region in self.regions:
            mutable_regions.append(region.get_mutable_copy(value_mapping=value_mapping, block_mapping=block_mapping))

        new_op: Operation = self.op_type.create(
            operands=mutable_operands,
            result_types=[result.typ for result in self.results],
            attributes=self.attributes.copy(),
            successors=mutable_successors,
            regions=mutable_regions)

        for idx, result in enumerate(self.results):
            m_result = new_op.results[idx]
            value_mapping[result] = m_result

        return new_op

    @classmethod
    def _create_new(
        cls,
        op_type: OperationType,
        immutable_operands: Optional[List[ImmutableSSAValue]] = None,
        result_types: Optional[List[Attribute]] = None,
        attributes: Optional[Dict[str, Attribute]] = None,
        successors: Optional[List[ImmutableBlock]] = None,
        regions: Optional[List[ImmutableRegion]] = None,
        value_map: Optional[Dict[SSAValue, ImmutableSSAValue]] = None,
        block_map: Optional[Dict[Block, ImmutableBlock]] = None
    ) -> Tuple[List[ImmutableOperation], List[Operation]]:
        """Creates new mutable operations and returns an immutable view on them."""

        if immutable_operands is None:
            immutable_operands = []
        if result_types is None:
            result_types = []
        if attributes is None:
            attributes = {}  # = original_mutable_op.attributes.copy()
        if successors is None:
            successors = []  # original_mutable_op.successors
        if regions is None:
            regions = []
        if value_map is None:
            value_map = {}
        if block_map is None:
            block_map = {}

        dependant_imm_operations: List[ImmutableOperation] = []
        dependant_operations: List[Operation] = []
        operands = []

        for idx, imm_operand in enumerate(immutable_operands):
            # if imm_operand in value_map:
            #     immutable_operands[idx] = value_map[imm_operand]
            #     imm_operand = immutable_operands[idx]
            if isinstance(
                imm_operand, ImmutableOpResult) and (op := imm_operand.get_op(
                )) is not None and op.parent_block is not None:
                # parent block set means we have to clone the op
                clonedOps = ImmutableOperation._create_new(
                    op._op.__class__,
                    immutable_operands=list(op.operands),
                    result_types=[result.typ for result in op._op.results],
                    attributes=op._op.attributes.copy(),
                    successors=list(op.successors),
                    regions=list(op.regions),
                    value_map=value_map,
                    block_map=block_map)

                dependant_imm_operations.extend(clonedOps[0])
                dependant_operations.extend(clonedOps[1])
                operands.append(clonedOps[0][-1].results[
                    imm_operand.result_index].get_mutable())
            elif isinstance(block_arg := imm_operand, ImmutableBlockArgument):
                # New Block args are created if not previously done so the parent
                # block can be build with them
                if block_arg.get_mutable() in value_map:
                    operands.append(value_map[block_arg.get_mutable()])
                else:
                    new_block_arg = ImmutableBlockArgument(
                        block_arg.typ, None, block_arg.index)
                    value_map[block_arg.get_mutable()] = new_block_arg
                    operands.append(block_arg.get_mutable())
            else:
                operands.append(imm_operand.get_mutable())

        # TODO: get Regions from the ImmutableRegions
        mutable_regions = []
        for region in regions:
            if region.parent_op is not None:
                # region has to be recreated
                mutable_regions.append(
                    ImmutableRegion._create_new(region, value_map,
                                                block_map)[1])
            else:
                mutable_regions.append(region._region)

        # successors is ImmutableBlock, not Block here!
        newOp: Operation = op_type.create(
            operands=list(operands),
            result_types=result_types,
            attributes=attributes,
            successors=[successor._block for successor in successors],
            regions=mutable_regions)

        return (dependant_imm_operations + [
            ImmutableOperation.from_op(newOp, value_map, block_map,
                                       immutable_operands)
        ]), dependant_operations + [newOp]

    @classmethod
    def create_new(
        cls,
        op_type: OperationType,
        immutable_operands: Optional[List[ImmutableSSAValue]] = None,
        result_types: Optional[List[Attribute]] = None,
        attributes: Optional[Dict[str, Attribute]] = None,
        successors: Optional[List[ImmutableBlock]] = None,
        regions: Optional[List[ImmutableRegion]] = None
    ) -> List[ImmutableOperation]:
        return cls._create_new(op_type, immutable_operands, result_types,
                               attributes, successors, regions)[0]

    @staticmethod
    def from_op(
        op: Operation,
        value_map: Optional[Dict[SSAValue, ImmutableSSAValue]] = None,
        block_map: Optional[Dict[Block, ImmutableBlock]] = None,
        existing_operands: Optional[List[ImmutableSSAValue]] = None
    ) -> ImmutableOperation:
        """creates an immutable view on an existing mutable op and all nested regions"""
        assert isinstance(op, Operation)
        op_type = op.__class__

        if value_map is None:
            value_map = {}
        if block_map is None:
            block_map = {}

        operands: List[ImmutableSSAValue] = []
        if existing_operands is None:
            for operand in op.operands:
                match operand:
                    case OpResult():
                        operands.append(
                            ImmutableOpResult(
                                operand.typ,
                                value_map[operand].op  # type: ignore
                                if operand in value_map else
                                ImmutableOperation.from_op(operand.op),
                                operand.result_index))
                    case BlockArgument():
                        if operand not in value_map:
                            raise Exception(
                                "Block argument expected in mapping")
                        operands.append(value_map[operand])
                    case _:
                        raise Exception(
                            "Operand is expeected to be either OpResult or BlockArgument"
                        )
        else:
            operands.extend(existing_operands)

        results: List[ImmutableOpResult] = []
        for idx, result in enumerate(op.results):
            results.append(immutable_result := ImmutableOpResult(
                result.typ, None, result.result_index))  # type: ignore
            value_map[result] = immutable_result

        attributes: Dict[str, Attribute] = op.attributes.copy()

        successors: List[ImmutableBlock] = []
        for successor in op.successors:
            if successor in block_map:
                successors.append(block_map[successor])
            else:
                newImmutableSuccessor = ImmutableBlock.from_block(successor)
                block_map[successor] = newImmutableSuccessor
                successors.append(newImmutableSuccessor)

        regions: List[ImmutableRegion] = []
        for region in op.regions:
            regions.append(ImmutableRegion.from_block_list(region.blocks))

        immutableOp = ImmutableOperation("immutable." + op.name,
                                         op_type,
                                         FrozenList(operands),
                                         FrozenList(results),
                                         attributes,
                                         FrozenList(successors),
                                         FrozenList(regions))

        return immutableOp

    def get_attribute(self, name: str) -> Attribute:
        return self.attributes[name]

    def get_attributes_copy(self) -> Dict[str, Attribute]:
        return self.attributes.copy()

    def walk(self, fun: Callable[[ImmutableOperation], None]) -> None:
        fun(self)
        for region in self.regions:
            region.walk(fun)

    # def get_mutable_copy(self) -> Operation:
    #     return self._op.clone()

    # def replace_with(self, ops: List[ImmutableOperation]):
    #     assert (isinstance(ops, List))
    #     assert (all([isinstance(op, ImmutableOperation) for op in ops]))
    #     rewriter = Rewriter()
    #     rewriter.replace_op(self._op, [op._op for op in ops])


def isa(op: Optional[ImmutableOperation], SomeOpClass: type[Operation]):
    if op is not None and op.op_type == SomeOpClass:
        return True
    else:
        return False