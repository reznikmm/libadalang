from __future__ import absolute_import

from langkit import compiled_types
from langkit.compiled_types import (
    ASTNode, BoolType, EquationType, Field, LexicalEnvType,
    LogicVarType, LongType, Struct, T, UserField, abstract,
    env_metadata, has_abstract_list, root_grammar_class, Symbol
)

from langkit.envs import EnvSpec, add_to_env
from langkit.expressions import (
    AbstractKind, AbstractProperty, And, Bind, EmptyArray, EmptyEnv, Env,
    EnvGroup, If, Let, Literal, New, No, Not, Or, Property, Self, Var,
    ignore, langkit_property
)
from langkit.expressions.analysis_units import (
    AnalysisUnitKind, AnalysisUnitType, UnitBody, UnitSpecification
)
from langkit.expressions.logic import (
    Predicate, LogicTrue
)


def symbol_list(base_id_list):
    """
    Turn a list of BaseId into the corresponding array of symbols.

    :param AbstractExpression base_id_list: ASTList for the BaseId nodes to
        process.
    :rtype: AbstractExpression
    """
    return base_id_list.map(lambda base_id: base_id.tok.symbol)


def is_library_item(e):
    """
    Property helper to determine if an entity is the root entity for its unit.
    """
    return e.parent.then(lambda p: p.match(
        lambda _=T.LibraryItem: True,
        lambda gen_pkg_decl=T.GenericPackageDecl:
            gen_pkg_decl.parent.then(lambda p: p.is_a(LibraryItem)),
        lambda _: False,
    ))


def get_library_item(unit):
    """
    Property helper to get the library unit corresponding to "unit".
    """
    return unit.root.then(
        lambda root:
            root.cast_or_raise(T.CompilationUnit).body
                .cast_or_raise(T.LibraryItem).item
    )


def is_package(e):
    """
    Property helper to determine if an entity is a package or not.

    :type e: AbstractExpression
    :rtype: AbstractExpression
    """
    return Not(e.is_null) & e.is_a(PackageDecl, PackageBody)


def is_library_package(e):
    """
    Property helper to determine if an entity is a library level package or
    not.

    :type e: AbstractExpression
    :rtype: AbstractExpression
    """
    return Not(e.is_null) & is_package(e) & is_library_item(e)


def decl_enclosing_scope(d):
    """
    Property helper to return the enclosing scope for the "d" declaration.

    This returns the closest parent that is a package (decl and body), a
    subprogram body or a block statement. Return null if there is no such
    parent.
    """
    return d.parent.parents.filter(
        lambda p: Or(
            is_package(p),
            p.is_a(SubpBody, BlockStmt)
        )
    ).at(0)


def canonical_type_or_null(type_expr):
    """
    If "type_expr" is null, return null, otherwise return its canonical type
    declaration.
    """
    return type_expr._.designated_type.canonical_type


def body_scope_decls(d):
    """
    Property helper to return a list of declarations for the body corresponding
    to scope for the given "d" declaration.
    """
    return decl_enclosing_scope(d).match(
        lambda pkg_decl=T.BasePackageDecl:
            pkg_decl.body_part.decls.decls.as_array,
        lambda pkg_body=T.PackageBody:
            pkg_body.decls.decls.as_array,
        lambda subp_body=T.SubpBody:
            subp_body.decls.decls.as_array,
        lambda block_stmt=T.BlockStmt:
            block_stmt.decls.decls.as_array,
        lambda _: EmptyArray(T.AdaNode),
    )


def decl_scope_decls(d):
    """
    Property helper to return a list of declarations for the body corresponding
    to scope for the given "d" declaration.
    """
    return decl_enclosing_scope(d).match(
        lambda pkg_decl=T.BasePackageDecl:
            pkg_decl.public_part.decls.as_array,
        lambda pkg_body=T.PackageBody:
            Let(lambda decl=pkg_body.decl_part:
                Let(lambda public_decls=decl.public_part.decls.as_array:
                    decl.private_part.then(
                        lambda private_part:
                            public_decls.concat(private_part.decls.as_array),
                        default_val=public_decls
                    ))),
        lambda subp_body=T.SubpBody:
            subp_body.decls.decls.as_array,
        lambda block_stmt=T.BlockStmt:
            block_stmt.decls.decls.as_array,
        lambda _: EmptyArray(T.AdaNode),
    )


def subp_body_from_spec(decl, subp_spec):
    """
    Property helper. Return the SubpBody node corresponding to "decl", which is
    a (Generic)SubpDecl node that contains "subp_spec".
    """
    return If(
        is_library_item(decl),

        get_library_item(decl.body_unit).cast_or_raise(T.SubpBody),

        body_scope_decls(decl).keep(T.SubpBody).filter(
            lambda subp_body:
            subp_body.subp_spec.match_signature(subp_spec)).at(0)
    )


@env_metadata
class Metadata(Struct):
    dottable_subp = UserField(
        BoolType, doc="Whether the stored element is a subprogram accessed "
                      "through the dot notation"
    )
    implicit_deref = UserField(
        BoolType, doc="Whether the stored element is accessed through an "
                      "implicit dereference"
    )


@abstract
@root_grammar_class(generic_list_type='AdaList')
class AdaNode(ASTNode):
    """
    Root node class for the Ada grammar. This is good and necessary for several
    reasons:

    1. It will facilitate the sharing of langkit_support code if we ever have
       two libraries generated by LanguageKit in the same application.

    2. It allows to insert code specific to the ada root node, without
       polluting every LanguageKit node, and without bringing back the root
       ASTNode in the code templates.
    """

    type_val = Property(
        No(T.AdaNode.env_el()),
        doc="""
        This will return the value of the type of this node after symbol
        resolution. NOTE: For this to be bound, resolve_symbols needs to be
        called on the appropriate parent node first.
        """
    )
    ref_val = Property(
        No(T.AdaNode.env_el()),
        doc="""
        This will return the node this nodes references after symbol
        resolution. NOTE: For this to be bound, resolve_symbols needs to be
        called on the appropriate parent node first.
        """
    )

    @langkit_property(return_type=EquationType, private=True,
                      has_implicit_env=True)
    def xref_equation(origin_env=LexicalEnvType):
        """
        This is the base property for constructing equations that, when solved,
        will resolve symbols and types for every sub expression of the
        expression you call it on. Note that if you call that on any
        expression, in some context it might lack full information and return
        multiple solutions. If you want completely precise resolution, you must
        call that on the outermost node that supports xref_equation.
        """
        # TODO: Maybe this should eventually be an AbstractProperty, but during
        # the development of the xref engine, it is practical to have the
        # default implementation return null, so that we can fail gracefully.
        ignore(origin_env)
        return No(EquationType)

    xref_stop_resolution = Property(False, private=True)

    @langkit_property(return_type=EquationType, private=True,
                      has_implicit_env=True)
    def sub_equation(origin_env=LexicalEnvType):
        """
        Wrapper for xref_equation, meant to be used inside of xref_equation
        when you want to get the sub equation of a sub expression. It is
        used to change the behavior when xref_equation is called from
        another xref_equation call, or from the top level, so that we can do
        resolution in several steps.
        """
        return If(Self.xref_stop_resolution,
                  LogicTrue(),
                  Self.xref_equation(origin_env))

    @langkit_property(return_type=BoolType, private=True,
                      has_implicit_env=True)
    def resolve_symbols_internal(initial=BoolType):
        """
        Internal helper for resolve_symbols, implementing the recursive logic.
        """
        i = Var(If(initial | Self.xref_stop_resolution,
                   Self.xref_equation(Env)._.solve,
                   True))

        j = Self.children.all(lambda c: c.then(
            lambda c: c.resolve_symbols_internal(False), default_val=True
        ))
        return i & j

    xref_entry_point = Property(False, doc="""
        Designates entities that are entry point for the xref solving
        infrastructure. If this returns true, then xref_equation can be
        called on it.
    """)

    @langkit_property(return_type=BoolType)
    def resolve_symbols():
        """
        This will resolve symbols for this node. If the operation is
        successful, then type_var and ref_var will be bound on appropriate
        subnodes of the statement.
        """
        return Self.node_env.eval_in_env(Self.resolve_symbols_internal(True))

    @langkit_property(return_type=BoolType)
    def is_visible_from(other=T.AdaNode):
        return Self.children_env.is_visible_from(other.children_env)

    body_unit = Property(
        # TODO: handle units with multiple packages
        get_library_item(Self.unit).match(
            lambda pkg_spec=T.BasePackageDecl:
                pkg_spec.package_name.referenced_unit(UnitBody),
            lambda gen_pkg_decl=T.GenericPackageDecl:
                gen_pkg_decl.package_decl.package_name
                .referenced_unit(UnitBody),
            lambda pkg_body=T.PackageBody:
                pkg_body.unit,
            lambda subp_decl=T.SubpDecl:
                subp_decl.subp_spec.name.referenced_unit(UnitBody),
            lambda gen_subp_decl=T.GenericSubpDecl:
                gen_subp_decl.subp_spec.name.referenced_unit(UnitBody),
            lambda subp_body=T.SubpBody:
                subp_body.unit,
            lambda _: No(AnalysisUnitType),
        ),
        doc="""
        If this unit has a body, fetch and return it.
        """,
        private=True
    )

    spec_unit = Property(
        # TODO: handle units with multiple packages
        get_library_item(Self.unit).match(
            lambda pkg_spec=T.BasePackageDecl:
                pkg_spec.unit,
            lambda pkg_body=T.PackageBody:
                pkg_body.package_name.referenced_unit(UnitSpecification),
            lambda subp_decl=T.SubpDecl:
                subp_decl.unit,
            lambda subp_body=T.SubpBody:
                subp_body.subp_spec.name.referenced_unit(UnitSpecification),
            lambda _: No(AnalysisUnitType),
        ),
        doc="""
        If this unit has a spec, fetch and return it.
        """,
        private=True
    )

    std = Property(
        Self.unit.root.node_env.get('standard').at(0).el,
        private=True,
        doc="""
        Retrieves the standard unit. Used to access standard types.
        """
    )

    std_entity = Property(
        lambda sym=Symbol: Self.std.children_env.get(sym).at(0).el,
        doc="Return an entity from the standard package with name `sym`",
        private=True
    )

    bool_type = Property(Self.std_entity('Boolean'), private=True)


def child_unit(name_expr, scope_expr):
    """
    This macro will add the properties and the env specification necessary
    to make a node implement the specification of a library child unit in
    Ada, so that you can declare new childs to an unit outside of its own
    scope.

    :param AbstractExpression name_expr: The expression that will retrieve
        the name symbol for the decorated node.

    :param AbstractExpression scope_expr: The expression that will retrieve the
        scope node for the decorated node. If the scope node is not found, it
        should return EmptyEnv: in this case, the actual scope will become the
        root environment.

    :rtype: EnvSpec
    """

    return EnvSpec(
        initial_env=Let(
            lambda scope=scope_expr: If(scope == EmptyEnv, Env, scope)
        ),
        add_env=True,
        add_to_env=add_to_env(name_expr, Self),
        env_hook_arg=Self,
    )


@abstract
class BasicDecl(AdaNode):
    defining_names = AbstractProperty(type=T.Name.array_type())
    defining_name = Property(Self.defining_names.at(0))
    defining_env = Property(
        EmptyEnv, private=True,
        doc="""
        Return a lexical environment that contains entities that are accessible
        as suffixes when Self is a prefix.
        """
    )

    array_ndims = Property(
        Literal(0),
        doc="""
        If this designates an entity with an array-like interface, return its
        number of dimensions. Return 0 otherwise.
        """
    )

    is_array = Property(Self.array_ndims > 0)
    is_subp = Property(Self.is_a(T.BasicSubpDecl, T.SubpBody))

    expr_type = Property(
        Self.type_expression._.designated_type,
        type=T.BaseTypeDecl,
        doc="""
        Return the type declaration corresponding to this basic declaration
        has when it is used in an expression context. For example, for this
        basic declaration::

            type Int is range 0 .. 100;

            A : Int := 12;

        the declaration of the Int type will be returned. For this
        declaration::

            type F is delta 0.01 digits 10;

            function B return F;

        expr_type will return the declaration of the type F.
        """
    )

    type_expression = Property(
        No(T.TypeExpr),
        type=T.TypeExpr,
        doc="""
        Return the type expression for this BasicDecl if applicable, a null
        otherwise.
        """
    )

    type_designator = Property(Let(lambda te=Self.type_expression: If(
        Not(te.is_null), te.cast(AdaNode), Self.expr_type.cast(AdaNode),
    )), doc="""
        Return the type designator for this BasicDecl. This will either be a
        TypeExpr instance, if applicable to this BasicDecl, or a
        BaseTypeDecl.
        """
    )

    array_def = Property(
        Self.expr_type.array_def,
        doc="""
        Return the ArrayTypeDef instance corresponding to this basic
        declaration.
        """
    )

    @langkit_property(return_type=T.BaseTypeDecl)
    def canonical_expr_type():
        """
        Same as expr_type, but will instead return the canonical type
        declaration.
        """
        return Self.expr_type._.canonical_type

    @langkit_property(return_type=T.SubpSpec)
    def subp_spec_or_null():
        """
        If node is a Subp, returns the specification of this subprogram.
        TODO: Enhance when we have interfaces.
        """
        return Self.match(
            lambda subp=BasicSubpDecl: subp.subp_spec,
            lambda subp=SubpBody:      subp.subp_spec,
            lambda _:                  No(SubpSpec),
        )

    @langkit_property(return_type=EquationType, private=True)
    def constrain_prefix(prefix=T.Expr):
        """
        This method is used when self is a candidate suffix in a dotted
        expression, to express the potential constraint that the suffix could
        express on the prefix.

        For example, given this code::

            1 type P is record
            2     A, B : Integer;
            3 end record;
            4
            5 P_Inst : P;
            7
            8 P_Inst.A;
              ^^^^^^^^

        A references the A ComponentDecl at line 2, and the constraint that we
        want to express on the prefix (P_Inst), is that it needs to be of type
        P.
        """
        # Default implementation returns logic true => does not add any
        # constraint to the xref equation.
        ignore(prefix)
        return LogicTrue()

    declarative_scope = Property(
        Self.parents.find(
            lambda p: p.is_a(T.DeclarativePart)
        ).cast(T.DeclarativePart),
        doc="Return the scope of definition of this basic declaration."
    )


@abstract
class Body(BasicDecl):
    pass


@abstract
class BodyStub(Body):
    pass


class DiscriminantSpec(BasicDecl):
    ids = Field(type=T.Identifier.list_type())
    type_expr = Field(type=T.TypeExpr)
    default_expr = Field(type=T.Expr)

    env_spec = EnvSpec(add_to_env=add_to_env(symbol_list(Self.ids), Self))

    defining_names = Property(Self.ids.map(lambda id: id.cast(T.Name)))


@abstract
class DiscriminantPart(AdaNode):
    pass


class KnownDiscriminantPart(DiscriminantPart):
    discr_specs = Field(type=T.DiscriminantSpec.list_type())


class UnknownDiscriminantPart(DiscriminantPart):
    pass


@abstract
class TypeDef(AdaNode):
    array_ndims = Property(
        Literal(0),
        doc="""
        If this designates an array type, return its number of dimensions.
        Return 0 otherwise.
        """
    )

    is_real_type = Property(False, doc="Whether type is a real type or not.")
    is_int_type = Property(False,
                           doc="Whether type is an integer type or not.")
    is_access_type = Property(False,
                              doc="Whether type is an access type or not.")
    is_char_type = Property(False)

    accessed_type = Property(No(T.BaseTypeDecl))
    is_tagged_type = Property(False, doc="Whether type is tagged or not")
    base_type = Property(
        No(T.BaseTypeDecl), doc="""
        Return the base type entity for this derived type definition.
        """
    )

    defining_env = Property(EmptyEnv)


class Variant(AdaNode):
    choice_list = Field(type=T.AdaNode.list_type())
    components = Field(type=T.ComponentList)


class VariantPart(AdaNode):
    discr_name = Field(type=T.Identifier)
    variant = Field(type=T.Variant.list_type())


@abstract
class BaseFormalParamDecl(BasicDecl):
    """
    Base class for formal parameter declarations. This is used both for records
    components and for subprogram parameters.
    """
    identifiers = AbstractProperty(type=T.BaseId.array_type())
    type_expression = AbstractProperty(type=T.TypeExpr, runtime_check=True)
    is_mandatory = Property(False)

    type = Property(Self.type_expression.designated_type.canonical_type)


class ComponentDecl(BaseFormalParamDecl):
    ids = Field(type=T.Identifier.list_type())
    component_def = Field(type=T.ComponentDef)
    default_expr = Field(type=T.Expr)
    aspects = Field(type=T.AspectSpec)

    env_spec = EnvSpec(add_to_env=add_to_env(symbol_list(Self.ids), Self))

    identifiers = Property(Self.ids.map(lambda e: e.cast(BaseId)))
    defining_env = Property(
        Self.component_def.type_expr.defining_env,
        private=True,
        doc="See BasicDecl.defining_env"
    )

    defining_names = Property(Self.ids.map(lambda id: id.cast(T.Name)))
    array_ndims = Property(Self.component_def.type_expr.array_ndims)

    type_expression = Property(Self.component_def.type_expr)

    @langkit_property(return_type=EquationType, private=True)
    def constrain_prefix(prefix=T.Expr):
        return (
            # Simple type equivalence
            Bind(prefix.type_var, Self.container_type,
                 eq_prop=BaseTypeDecl.fields.matching_prefix_type)
        )

    @langkit_property(return_type=T.BaseTypeDecl)
    def container_type():
        """
        Return the defining container type for this component declaration.
        """
        return Self.parents.find(
            lambda p: p.is_a(BaseTypeDecl)
        ).cast(BaseTypeDecl)


@abstract
class BaseFormalParamHolder(AdaNode):
    """
    Base class for lists of formal parameters. This is used both for subprogram
    specifications and for records, so that we can share the matching and
    unpacking logic.
    """

    abstract_formal_params = AbstractProperty(
        type=BaseFormalParamDecl.array_type(),
        doc="Return the list of abstract formal parameters for this holder."
    )

    unpacked_formal_params = Property(
        Self.abstract_formal_params.mapcat(
            lambda spec: spec.identifiers.map(lambda id: (
                New(SingleFormal, name=id, spec=spec)
            ))
        ),
        doc='Couples (identifier, param spec) for all parameters'
    )

    @langkit_property(return_type=T.ParamMatch.array_type(),
                      has_implicit_env=True)
    def match_param_list(params=T.AssocList, is_dottable_subp=BoolType):
        """
        For each ParamAssoc in a AssocList, return whether we could find a
        matching formal in Self, and whether this formal is optional (i.e. has
        a default value).
        """
        def matches(formal, actual):
            return New(ParamMatch,
                       has_matched=True,
                       formal=formal,
                       actual=actual)

        unpacked_formals = Var(Self.unpacked_formal_params)

        return params.unpacked_params.map(lambda i, a: If(
            a.name.is_null,

            Let(lambda idx=If(is_dottable_subp, i + 1, i):
                # Positional parameter case: if this parameter has no
                # name association, make sure we have enough formals.
                unpacked_formals.at(idx).then(lambda sp: matches(sp, a))),

            # Named parameter case: make sure the designator is
            # actualy a name and that there is a corresponding
            # formal.
            a.name.then(lambda id: (
                unpacked_formals.find(lambda p: p.name.matches(id)).then(
                    lambda sp: matches(sp, a)
                )
            ))
        ))


class ComponentList(BaseFormalParamHolder):
    components = Field(type=T.AdaNode.list_type())
    variant_part = Field(type=T.VariantPart)

    type_def = Property(Self.parent.parent.cast(T.TypeDef))

    parent_component_list = Property(
        Self.type_def.cast(T.DerivedTypeDef)._.base_type.record_def.components
    )

    @langkit_property()
    def abstract_formal_params():
        # TODO: Incomplete definition. We need to handle variant parts.
        pcl = Var(Self.parent_component_list)

        self_comps = Var(Self.components.keep(BaseFormalParamDecl))

        return If(
            pcl.is_null,
            self_comps,
            pcl.abstract_formal_params.concat(self_comps)
        )


@abstract
class BaseRecordDef(AdaNode):
    components = Field(type=T.ComponentList)


class RecordDef(BaseRecordDef):
    pass


class NullRecordDef(BaseRecordDef):
    pass


class Tagged(T.EnumNode):
    qualifier = True


class Abstract(T.EnumNode):
    qualifier = True


class Limited(T.EnumNode):
    qualifier = True


class Private(T.EnumNode):
    qualifier = True


class Aliased(T.EnumNode):
    qualifier = True


class NotNull(T.EnumNode):
    qualifier = True


class Constant(T.EnumNode):
    qualifier = True


class All(T.EnumNode):
    qualifier = True


class Abort(T.EnumNode):
    qualifier = True


class Reverse(T.EnumNode):
    qualifier = True


class WithPrivate(T.EnumNode):
    qualifier = True


class Until(T.EnumNode):
    qualifier = True


class Synchronized(T.EnumNode):
    qualifier = True


class Protected(T.EnumNode):
    qualifier = True


class RecordTypeDef(TypeDef):
    has_abstract = Field(type=Abstract)
    has_tagged = Field(type=Tagged)
    has_limited = Field(type=Limited)
    record_def = Field(type=T.BaseRecordDef)

    defining_env = Property(
        # We don't want to be able to access env elements in parents,
        # so we orphan the env.
        Self.children_env.env_orphan,
        type=LexicalEnvType
    )

    is_tagged_type = Property(Self.has_tagged.as_bool)


@abstract
class RealTypeDef(TypeDef):
    is_real_type = Property(True)


@abstract
class BaseTypeDecl(BasicDecl):
    type_id = Field(type=T.Identifier)

    name = Property(Self.type_id)
    env_spec = EnvSpec(
        add_to_env=add_to_env(Self.type_id.relative_name.symbol, Self)
    )

    defining_names = Property(Self.type_id.cast(T.Name).singleton)

    is_real_type = Property(False, doc="Whether type is a real type or not.")
    is_int_type = Property(False, doc="Whether type is an integer type or not")

    is_access_type = Property(False,
                              doc="Whether type is an access type or not")

    is_char_type = Property(False,
                            doc="Whether type is a character type or not")

    is_str_type = Property(Self.is_array & Self.comp_type._.is_char_type)

    accessed_type = Property(No(T.BaseTypeDecl))
    is_tagged_type = Property(False, doc="Whether type is tagged or not")
    base_type = Property(
        No(T.BaseTypeDecl), doc="""
        Return the base type entity for this derived type declaration.
        """
    )
    array_def = Property(No(T.ArrayTypeDef))
    record_def = Property(No(T.BaseRecordDef))

    comp_type = Property(
        Self.array_def._.comp_type,
        doc="""
        Return the component type of the type, if applicable. The
        component type is the type you'll get if you call an instance of the
        Self type. So it can either be:
        1. The component type for an array
        2. The return type for an access to function
        """
    )

    # A BaseTypeDecl in an expression context corresponds to a type conversion,
    # so its type is itself.
    expr_type = Property(Self)

    @langkit_property(return_type=BoolType)
    def is_derived_type(other_type=T.BaseTypeDecl):
        """
        Whether Self is derived from other_type.
        """
        return Or(
            Self == other_type,
            (Not(Self.classwide_type.is_null)
             & (Self.classwide_type == other_type.classwide_type)),
            Self.base_type._.is_derived_type(other_type)
        )

    is_iterable_type = Property(
        # TODO: Only works with array types at the moment, need to implement
        # on:
        # Spark iterable types (Iterable aspect).
        # Ada 2012 iterable types.
        Self.is_array,
        doc="""
        Whether Self is a type that is iterable in a for .. of loop
        """
    )

    @langkit_property(return_type=BoolType)
    def matching_prefix_type(container_type=T.BaseTypeDecl):
        """
        Given a dotted expression A.B, where container_type is the container
        type for B, and Self is a potential type for A, returns whether Self is
        a valid type for A in the dotted expression.
        """
        cont_type = Var(container_type.canonical_type)
        return Or(
            # Derived type case
            Self.canonical_type.is_derived_type(cont_type),

            # Access to derived type case
            Self.canonical_type.accessed_type._.is_derived_type(cont_type),
        )

    @langkit_property(return_type=BoolType)
    def matching_access_type(expected_type=T.BaseTypeDecl):
        """
        Whether self is a matching access type for expected_type.
        """
        actual_type = Var(Self)
        return expected_type.match(
            lambda atd=T.AnonymousTypeDecl:
            atd.access_def_matches(actual_type),
            lambda _: False
        )

    @langkit_property(return_type=BoolType)
    def matching_type(expected_type=T.BaseTypeDecl):
        actual_type = Var(Self)
        return Or(
            And(
                expected_type.is_classwide,
                actual_type.is_derived_type(expected_type)
            ),
            actual_type == expected_type,
            actual_type.matching_access_type(expected_type)
        )

    @langkit_property(return_type=BoolType)
    def matching_allocator_type(allocated_type=T.BaseTypeDecl):
        return And(
            Self.is_access_type,
            allocated_type.matching_type(Self.accessed_type)
        )

    @langkit_property(return_type=T.BaseTypeDecl)
    def canonical_type():
        """
        Return the canonical type declaration for this type declaration. For
        subtypes, it will return the base type declaration.
        """
        return Self

    classwide_type = Property(If(
        Self.is_tagged_type,
        New(T.ClasswideTypeDecl, type_id=Self.type_id),
        No(T.ClasswideTypeDecl)
    ), memoized=True)

    is_classwide = Property(False)


class ClasswideTypeDecl(BaseTypeDecl):
    """
    Synthetic node (not parsed, generated from a property call). Refers to the
    classwide type for a given tagged type. The aim is that those be mostly
    equivalent to their non-classwide type, except for some resolution rules.
    """
    # We don't want to add the classwide type to the environment
    env_spec = EnvSpec(call_parents=False)

    typedecl = Property(Self.parent.cast(BaseTypeDecl))

    is_classwide = Property(True)

    is_tagged_type = Property(True)
    base_type = Property(Self.typedecl.base_type)
    record_def = Property(Self.typedecl.record_def)
    classwide_type = Property(Self)
    is_iterable_type = Property(Self.typedecl.is_iterable_type)
    defining_env = Property(Self.typedecl.defining_env)


class TypeDecl(BaseTypeDecl):
    discriminants = Field(type=T.DiscriminantPart)
    type_def = Field(type=T.TypeDef)
    aspects = Field(type=T.AspectSpec)

    array_ndims = Property(Self.type_def.array_ndims)

    is_real_type = Property(Self.type_def.is_real_type)
    is_int_type = Property(Self.type_def.is_int_type)
    is_access_type = Property(Self.type_def.is_access_type)
    accessed_type = Property(Self.type_def.accessed_type)
    is_tagged_type = Property(Self.type_def.is_tagged_type)
    base_type = Property(Self.type_def.base_type)

    array_def = Property(Self.type_def.cast(T.ArrayTypeDef))

    defining_env = Property(
        # Evaluating in type env, because the defining environment of a type
        # is always its own.
        Self.children_env.eval_in_env(Self.type_def.defining_env)

        # TODO: The fact that the env should not be inherited from the called
        # property might be common enough to warrant a specific construct, such
        # as a kw parameter to Property: inherit_caller_env=False - or even
        # make it the default, and only inherit the env when explicitly
        # specified.
    )

    env_spec = EnvSpec(add_env=True)

    record_def = Property(
        Self.type_def.match(
            lambda r=T.RecordTypeDef: r.record_def,
            lambda d=T.DerivedTypeDef: d.record_extension,
            lambda _: No(T.BaseRecordDef)
        )
    )


class AnonymousTypeDecl(TypeDecl):

    @langkit_property(return_type=BoolType)
    def access_def_matches(other=BaseTypeDecl):
        """
        Returns whether:
        1. Self and other are both access types.
        2. Their access def matches structurally.
        """

        # If the anonymous type is an access type definition, then verify if
        #  the accessed type corresponds to other's accessed type.
        return Self.type_def.cast(AccessDef)._.accessed_type.matching_type(
            other.accessed_type
        )

    # We don't want to add anonymous type declarations to the lexical
    # environments, so we reset the env spec.
    env_spec = EnvSpec(call_parents=False)


class EnumTypeDecl(BaseTypeDecl):
    enum_literals = Field(type=T.EnumLiteralDecl.list_type())
    aspects = Field(type=T.AspectSpec)

    is_char_type = Property(Self.enum_literals.any(
        lambda lit: lit.enum_identifier.is_a(T.CharLiteral)
    ))


class FloatingPointDef(RealTypeDef):
    num_digits = Field(type=T.Expr)
    range = Field(type=T.Expr)


class OrdinaryFixedPointDef(RealTypeDef):
    delta = Field(type=T.Expr)
    range = Field(type=T.Expr)


class DecimalFixedPointDef(RealTypeDef):
    delta = Field(type=T.Expr)
    digits = Field(type=T.Expr)
    range = Field(type=T.Expr)


@abstract
class Constraint(AdaNode):
    pass


class RangeConstraint(Constraint):
    range = Field(type=T.Expr)


class DigitsConstraint(Constraint):
    digits = Field(type=T.Expr)
    range = Field(type=T.Expr)


class DeltaConstraint(Constraint):
    digits = Field(type=T.Expr)
    range = Field(type=T.Expr)


class IndexConstraint(Constraint):
    constraints = Field(type=T.AdaNode.list_type())


class DiscriminantConstraint(Constraint):
    constraints = Field(type=T.DiscriminantAssoc.list_type())


class DiscriminantAssoc(Constraint):
    ids = Field(type=T.Identifier.list_type())
    expr = Field(type=T.Expr)


class DerivedTypeDef(TypeDef):
    has_abstract = Field(type=Abstract)
    has_limited = Field(type=Limited)
    has_synchronized = Field(type=Synchronized)
    subtype_indication = Field(type=T.SubtypeIndication)
    interfaces = Field(type=T.Name.list_type())
    record_extension = Field(type=T.BaseRecordDef)
    has_with_private = Field(type=WithPrivate)

    array_ndims = Property(Self.base_type.array_ndims)

    base_type = Property(Self.subtype_indication.designated_type)

    is_real_type = Property(Self.base_type.is_real_type)
    is_int_type = Property(Self.base_type.is_int_type)
    is_access_type = Property(Self.base_type.is_access_type)
    is_char_type = Property(Self.base_type.is_char_type)
    accessed_type = Property(Self.base_type.accessed_type)
    is_tagged_type = Property(True)

    defining_env = Property(EnvGroup(
        Self.children_env.env_orphan,

        # Add environments from parent type defs
        Self.base_type.canonical_type.defining_env
    ))


class IncompleteTypeDef(TypeDef):
    has_tagged = Field(type=Tagged)

    is_tagged_type = Property(Self.has_tagged.as_bool)
    # TODO: what should we return for array_ndims? Do we need to find the full
    # view?


class PrivateTypeDef(TypeDef):
    has_abstract = Field(type=Abstract)
    has_tagged = Field(type=Tagged)
    has_limited = Field(type=Limited)

    # TODO: what should we return for array_ndims? Do we need to find the full
    # view?


class SignedIntTypeDef(TypeDef):
    range = Field(type=T.Expr)
    is_int_type = Property(True)


class ModIntTypeDef(TypeDef):
    expr = Field(type=T.Expr)
    is_int_type = Property(True)


@abstract
class ArrayIndices(AdaNode):
    ndims = AbstractProperty(
        type=LongType,
        doc="""Number of dimensions described in this node."""
    )

    @langkit_property(private=True, return_type=EquationType)
    def constrain_index_expr(index_expr=T.Expr, dim=LongType):
        """
        Add a constraint on an expression passed as the index of an array
        access expression.

        For example::

            type A is array (Integer range 1 .. 10) of Integer;

            A_Inst : A;

            A_Inst (2);
            --      ^ Will add constraint on lit that it needs to be of type
            --      Integer.
        """
        ignore(index_expr, dim)
        return LogicTrue()


class UnconstrainedArrayIndices(ArrayIndices):
    types = Field(type=T.SubtypeIndication.list_type())
    ndims = Property(Self.types.length)

    @langkit_property(return_type=EquationType)
    def constrain_index_expr(index_expr=T.Expr, dim=LongType):
        return Bind(
            index_expr.type_var,
            Self.types.at(dim).designated_type.canonical_type
        )


class ConstrainedArrayIndices(ArrayIndices):
    list = Field(type=T.AdaNode.list_type())

    ndims = Property(Self.list.length)

    @langkit_property(return_type=EquationType)
    def constrain_index_expr(index_expr=T.Expr, dim=LongType):
        return Self.list.at(dim).match(
            lambda n=T.SubtypeIndication:
            Bind(index_expr.type_var, n.designated_type.canonical_type),

            # TODO: We need to parse Standard to express the fact that when
            # we've got an anonymous range in the array index definition,
            # the index needs to be of type Standard.Integer.
            lambda _: LogicTrue()
        )


class ComponentDef(AdaNode):
    has_aliased = Field(type=Aliased)
    type_expr = Field(type=T.TypeExpr)


class ArrayTypeDef(TypeDef):
    indices = Field(type=T.ArrayIndices)
    component_type = Field(type=T.ComponentDef)

    comp_type = Property(
        Self.component_type.type_expr.designated_type.canonical_type,
        doc="Returns the type stored as a component in the array"
    )

    array_ndims = Property(Self.indices.ndims)


class InterfaceKind(T.EnumNode):
    alternatives = ["limited", "task", "protected", "synchronized"]


class InterfaceTypeDef(TypeDef):
    interface_kind = Field(type=InterfaceKind)
    interfaces = Field(type=T.Name.list_type())


class SubtypeDecl(BaseTypeDecl):
    subtype = Field(type=T.SubtypeIndication)
    aspects = Field(type=T.AspectSpec)

    array_ndims = Property(Self.subtype.array_ndims)
    defining_env = Property(Self.subtype.defining_env)

    canonical_type = Property(Self.subtype.designated_type.canonical_type)

    accessed_type = Property(Self.canonical_type.accessed_type)


class TaskDef(AdaNode):
    interfaces = Field(type=T.Name.list_type())
    public_part = Field(type=T.PublicPart)
    private_part = Field(type=T.PrivatePart)
    end_id = Field(type=T.Identifier)


class ProtectedDef(AdaNode):
    public_part = Field(type=T.PublicPart)
    private_part = Field(type=T.PrivatePart)
    end_id = Field(type=T.Identifier)


class TaskTypeDecl(BasicDecl):
    task_type_name = Field(type=T.Identifier)
    discrs = Field(type=T.DiscriminantPart)
    aspects = Field(type=T.AspectSpec)
    definition = Field(type=T.TaskDef)

    defining_names = Property(Self.task_type_name.cast(T.Name).singleton)


class ProtectedTypeDecl(BasicDecl):
    protected_type_name = Field(type=T.Identifier)
    discrs = Field(type=T.DiscriminantPart)
    aspects = Field(type=T.AspectSpec)
    interfaces = Field(type=T.Name.list_type())
    definition = Field(type=T.ProtectedDef)

    defining_names = Property(Self.protected_type_name.cast(T.Name).singleton)


@abstract
class AccessDef(TypeDef):
    has_not_null = Field(type=NotNull)

    is_access_type = Property(True)
    accessed_type = Property(No(BaseTypeDecl))

    defining_env = Property(Self.accessed_type.defining_env)


class AccessToSubpDef(AccessDef):
    has_protected = Field(type=Protected, repr=False)
    subp_spec = Field(type=T.SubpSpec)


class TypeAccessDef(AccessDef):
    has_all = Field(type=All)
    has_constant = Field(type=Constant)
    subtype_indication = Field(type=T.SubtypeIndication)
    constraint = Field(type=T.Constraint)

    accessed_type = Property(Self.subtype_indication.designated_type)


class FormalDiscreteTypeDef(TypeDef):
    pass


class NullComponentDecl(AdaNode):
    pass


class WithClause(AdaNode):
    has_limited = Field(type=Limited)
    has_private = Field(type=Private)
    packages = Field(type=T.Name.list_type())

    env_spec = EnvSpec(env_hook_arg=Self)


@abstract
class UseClause(AdaNode):
    pass


class UsePackageClause(UseClause):
    packages = Field(type=T.Name.list_type())

    env_spec = EnvSpec(
        ref_envs=Self.packages.map(lambda package: package.designated_env(Env))
    )


class UseTypeClause(UseClause):
    has_all = Field(type=All)
    types = Field(type=T.Name.list_type())


@abstract
class TypeExpr(AdaNode):
    """
    A type expression is an abstract node that embodies the concept of a
    reference to a type.

    Since Ada has both subtype_indications and anonymous (inline) type
    declarations, a type expression contains one or the other.
    """

    array_def = Property(Self.designated_type.array_def)
    array_ndims = Property(Self.designated_type.array_ndims)
    comp_type = Property(Self.designated_type.comp_type)
    defining_env = Property(Self.designated_type.defining_env, private=True)
    accessed_type = Property(Self.designated_type.accessed_type)

    designated_type = AbstractProperty(
        type=BaseTypeDecl, runtime_check=True,
        doc="""
        Return the type designated by this type expression.
        """
    )

    is_anonymous_access = Property(False)

    @langkit_property(return_type=BaseTypeDecl)
    def element_type():
        """
        If self is an anonymous access, return the accessed type. Otherwise,
        return the designated type.
        """
        d = Self.designated_type
        return If(d.is_null, Self.accessed_type, d)


class AnonymousType(TypeExpr):
    """
    Container for inline anonymous array and access types declarations.
    """
    type_decl = Field(type=T.AnonymousTypeDecl)

    designated_type = Property(Self.type_decl)
    is_anonymous_access = Property(
        Self.type_decl.type_def.cast(T.AccessDef).then(lambda _: True)
    )


class SubtypeIndication(TypeExpr):
    has_not_null = Field(type=NotNull)
    name = Field(type=T.Name)
    constraint = Field(type=T.Constraint)

    # The name for this type has to be evaluated in the context of the
    # SubtypeIndication node itself: we don't want to use whatever lexical
    # environment the caller is using.
    designated_type = Property(
        Self.node_env.eval_in_env(Self.name.designated_type_impl)
    )

    @langkit_property()
    def xref_equation(origin_env=LexicalEnvType):
        # Called by allocator.xref_equation, since the suffix can be either a
        # qual expr or a subtype indication.
        ignore(origin_env)
        return LogicTrue()


class Mode(T.EnumNode):
    alternatives = ["in", "out", "in_out", "default"]


class ParamSpec(BaseFormalParamDecl):
    ids = Field(type=T.Identifier.list_type())
    has_aliased = Field(type=Aliased)
    mode = Field(type=Mode)
    type_expr = Field(type=T.TypeExpr)
    default = Field(type=T.Expr)

    identifiers = Property(Self.ids.map(lambda e: e.cast(BaseId)))
    is_mandatory = Property(Self.default.is_null)
    defining_names = Property(Self.ids.map(lambda id: id.cast(T.Name)))

    env_spec = EnvSpec(add_to_env=add_to_env(symbol_list(Self.ids), Self))

    type_expression = Property(Self.type_expr)


class AspectSpec(AdaNode):
    aspect_assocs = Field(type=T.AspectAssoc.list_type())


class Overriding(T.EnumNode):
    alternatives = ["overriding", "not_overriding", "unspecified"]


@abstract
class BasicSubpDecl(BasicDecl):
    overriding = Field(type=Overriding)
    subp_spec = Field(type=T.SubpSpec)

    name = Property(Self.subp_spec.name)
    defining_names = Property(Self.subp_spec.name.singleton)
    defining_env = Property(Self.subp_spec.defining_env)

    type_expression = Property(
        Self.subp_spec.returns, doc="""
        The expr type of a subprogram declaration is the return type of the
        subprogram if the subprogram is a function.
        """
    )

    env_spec = EnvSpec(
        initial_env=Self.subp_spec.name.parent_scope,
        add_to_env=[
            # First regular add to env action, adding to the subp's scope
            add_to_env(Self.subp_spec.name.relative_name.symbol, Self),

            # Second custom action, adding to the type's environment if the
            # type is tagged and self is a primitive of it.
            add_to_env(
                key=Self.subp_spec.name.relative_name.symbol,
                val=Self.subp_spec.dottable_subp,
                dest_env=Self.subp_spec.potential_dottable_type._.children_env,
                # We pass custom metadata, marking the entity as a dottable
                # subprogram.
                metadata=New(Metadata, dottable_subp=True,
                             implicit_deref=False),

                # potential_dottable_type will need the SubtypeIndication
                # instance to have an associated environment, so we need to do
                # this after environments have been populated for the children.
                is_post=True
            )
        ],
        add_env=True,

        # Call the env hook so that library-level subprograms have their
        # parent unit (if any) environment.
        env_hook_arg=Self,
    )


class SubpDecl(BasicSubpDecl):
    aspects = Field(type=T.AspectSpec)

    body_part = Property(
        subp_body_from_spec(Self, Self.subp_spec),
        doc="""
        Return the SubpBody corresponding to this node.
        """
    )


class NullSubpDecl(BasicSubpDecl):
    aspects = Field(type=T.AspectSpec)


class AbstractSubpDecl(BasicSubpDecl):
    aspects = Field(type=T.AspectSpec)


class ExprFunction(BasicSubpDecl):
    expr = Field(type=T.Expr)
    aspects = Field(type=T.AspectSpec)


class SubpRenamingDecl(BasicSubpDecl):
    renames = Field(type=T.RenamingClause)
    aspects = Field(type=T.AspectSpec)


class Pragma(AdaNode):
    id = Field(type=T.Identifier)
    args = Field(type=T.PragmaArgumentAssoc.list_type())


class PragmaArgumentAssoc(AdaNode):
    id = Field(type=T.Identifier)
    expr = Field(type=T.Expr)


@abstract
class AspectClause(AdaNode):
    pass


class EnumRepClause(AspectClause):
    type_name = Field(type=T.Name)
    aggregate = Field(type=T.Aggregate)


class AttributeDefClause(AspectClause):
    attribute_expr = Field(type=T.Expr)
    expr = Field(type=T.Expr)


class ComponentClause(AdaNode):
    id = Field(type=T.Identifier)
    position = Field(type=T.Expr)
    range = Field(type=T.Expr)


class RecordRepClause(AspectClause):
    component_name = Field(type=T.Name)
    at_expr = Field(type=T.Expr)
    components = Field(type=T.ComponentClause.list_type())


class AtClause(AspectClause):
    name = Field(type=T.BaseId)
    expr = Field(type=T.Expr)


class EntryDecl(BasicDecl):
    overriding = Field(type=Overriding)
    entry_id = Field(type=T.Identifier)
    family_type = Field(type=T.AdaNode)
    params = Field(type=T.ParamSpec.list_type())
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Self.entry_id.cast(T.Name).singleton)


class SingleTaskDecl(BasicDecl):
    task_name = Field(type=T.Identifier)
    aspects = Field(type=T.AspectSpec)
    definition = Field(type=T.TaskDef)

    defining_names = Property(Self.task_name.cast(T.Name).singleton)


class SingleProtectedDecl(BasicDecl):
    protected_name = Field(type=T.Identifier)
    aspects = Field(type=T.AspectSpec)
    interfaces = Field(type=T.Name.list_type())
    definition = Field(type=T.ProtectedDef)

    defining_names = Property(Self.protected_name.cast(T.Name).singleton)


class AspectAssoc(AdaNode):
    id = Field(type=T.Expr)
    expr = Field(type=T.Expr)


class NumberDecl(BasicDecl):
    ids = Field(type=T.Identifier.list_type())
    expr = Field(type=T.Expr)

    defining_names = Property(Self.ids.map(lambda id: id.cast(T.Name)))


class ObjectDecl(BasicDecl):
    ids = Field(type=T.Identifier.list_type())
    has_aliased = Field(type=Aliased)
    has_constant = Field(type=Constant)
    inout = Field(type=Mode)
    type_expr = Field(type=T.TypeExpr)
    default_expr = Field(type=T.Expr)
    renaming_clause = Field(type=T.RenamingClause)
    aspects = Field(type=T.AspectSpec)

    env_spec = EnvSpec(add_to_env=add_to_env(symbol_list(Self.ids), Self))

    array_ndims = Property(Self.type_expr.array_ndims)
    array_def = Property(Self.type_expr.array_def)

    defining_names = Property(Self.ids.map(lambda id: id.cast(T.Name)))

    defining_env = Property(Self.type_expr.defining_env)

    type_expression = Property(Self.type_expr)

    @langkit_property()
    def xref_equation(origin_env=LexicalEnvType):
        return (
            Self.default_expr.then(lambda de: de.xref_equation(origin_env),
                                   default_val=LogicTrue())
            & Bind(Self.default_expr.type_var,
                   Self.canonical_expr_type,
                   eq_prop=BaseTypeDecl.fields.matching_type)
        )

    xref_entry_point = Property(True)


class DeclarativePart(AdaNode):
    decls = Field(type=T.AdaNode.list_type())


class PrivatePart(DeclarativePart):
    env_spec = EnvSpec(add_env=True)


class PublicPart(DeclarativePart):
    pass


class BasePackageDecl(BasicDecl):
    """
    Package declarations. Concrete instances of this class
    will be created in generic package declarations. Other non-generic
    package declarations will be instances of PackageDecl.

    The behavior is the same, the only difference is that BasePackageDecl
    and PackageDecl have different behavior regarding lexical environments.
    In the case of generic package declarations, we use BasePackageDecl
    which has no env_spec, and the environment behavior is handled by the
    GenericPackageDecl instance.
    """
    package_name = Field(type=T.Name)
    aspects = Field(type=T.AspectSpec)
    public_part = Field(type=T.PublicPart)
    private_part = Field(type=T.PrivatePart)
    end_id = Field(type=T.Name)

    name = Property(Self.package_name, private=True)
    defining_names = Property(Self.name.singleton)
    defining_env = Property(Self.children_env.env_orphan)

    @langkit_property(return_type=T.PackageBody)
    def body_part():
        """
        Return the PackageBody corresponding to this node.
        """
        # Fetch the unit body even when we don't need it to make sure the body
        # (if it exists) is present in the environment.
        return Let(
            lambda body_unit=Self.body_unit:
                If(is_library_item(Self),

                   # If Self is a library-level package, then just fetch the
                   # root package in the body unit.
                   get_library_item(body_unit).cast(T.PackageBody),

                   # Self is a nested package: the name of such packages must
                   # be an identifier. Now, just use the __body link.
                   Self.children_env.get('__body', recursive=False)
                       .at(0).then(
                           lambda elt: elt.el.cast_or_raise(T.PackageBody)))
        )


class PackageDecl(BasePackageDecl):
    """
    Non-generic package declarations.
    """
    env_spec = child_unit(Self.package_name.relative_name.symbol,
                          Self.package_name.parent_scope)


class ExceptionDecl(BasicDecl):
    """
    Exception declarations.
    """
    ids = Field(type=T.Identifier.list_type())
    renames = Field(type=T.RenamingClause)
    aspects = Field(type=T.AspectSpec)
    defining_names = Property(Self.ids.map(lambda id: id.cast(T.Name)))

    env_spec = EnvSpec(
        add_to_env=add_to_env(Self.ids.map(lambda e: e.tok.symbol), Self)
    )


@abstract
class GenericInstantiation(BasicDecl):
    """
    Instantiations of generics.
    """
    pass


class GenericSubpInstantiation(GenericInstantiation):
    overriding = Field(type=Overriding)
    kind = Field(type=T.SubpKind)
    name = Field(type=T.Name)
    generic_entity_name = Field(type=T.Name)
    params = Field(type=T.AdaNode)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Self.name.singleton)


class GenericPackageInstantiation(GenericInstantiation):
    name = Field(type=T.Name)
    generic_entity_name = Field(type=T.Name)
    params = Field(type=T.AdaNode)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Self.name.singleton)


class RenamingClause(AdaNode):
    """
    Renaming clause, used everywhere renamings are valid.
    """
    renamed_object = Field(type=T.Expr)


class PackageRenamingDecl(BasicDecl):
    name = Field(type=T.Name)
    renames = Field(type=RenamingClause)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Self.name.singleton)


@abstract
class GenericRenamingDecl(BasicDecl):
    """
    Base node for all generic renaming declarations.
    """
    pass


class GenericPackageRenamingDecl(GenericRenamingDecl):
    name = Field(type=T.Name)
    renames = Field(type=T.Name)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Self.name.singleton)


class SubpKind(T.EnumNode):
    alternatives = ["procedure", "function"]


class GenericSubpRenamingDecl(GenericRenamingDecl):
    kind = Field(type=T.SubpKind)
    name = Field(type=T.Name)
    renames = Field(type=T.Name)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Self.name.singleton)


class FormalSubpDecl(BasicSubpDecl):
    """
    Formal subprogram declarations, in generic declarations formal parts.
    """
    has_abstract = Field(type=Abstract)
    default_value = Field(type=T.Expr)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Self.subp_spec.name.singleton)


class GenericFormalPart(BaseFormalParamHolder):
    decls = Field()

    abstract_formal_params = Property(
        Self.decls.keep(BaseFormalParamDecl)
    )


class GenericFormal(BaseFormalParamDecl):
    decl = Field(T.BasicDecl)
    identifiers = Property(
        Self.decl.defining_names.map(lambda p: p.cast_or_raise(T.BaseId))
    )
    defining_names = Property(Self.decl.defining_names)


class GenericSubpDecl(BasicDecl):
    env_spec = child_unit(Self.subp_spec.name.relative_name.symbol,
                          Self.subp_spec.name.parent_scope)

    formal_part = Field(type=T.GenericFormalPart)
    subp_spec = Field(type=T.SubpSpec)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Self.subp_spec.name.singleton)

    body_part = Property(
        subp_body_from_spec(Self, Self.subp_spec),
        doc="""
        Return the SubpBody corresponding to this node.
        """
    )


class GenericPackageDecl(BasicDecl):
    env_spec = child_unit(Self.package_name.relative_name.symbol,
                          Self.package_name.parent_scope)

    formal_part = Field(type=T.GenericFormalPart)
    package_decl = Field(type=BasePackageDecl)

    package_name = Property(Self.package_decl.package_name)

    defining_names = Property(Self.package_name.singleton)

    @langkit_property()
    def body_part():
        """
        Return the PackageBody corresponding to this node, or null if there is
        none.
        """
        return Self.package_decl.body_part


@abstract
class Expr(AdaNode):

    type_var = UserField(LogicVarType, is_private=True)
    type_val = Property(Self.type_var.get_value)

    @langkit_property(kind=AbstractKind.abstract_runtime_check, private=True,
                      return_type=LexicalEnvType, has_implicit_env=True)
    def designated_env(origin_env=LexicalEnvType):
        """
        Returns the lexical environment designated by this name.

        If this name involves overloading, this will return a combination of
        the various candidate lexical environments.
        """
        pass

    parent_scope = AbstractProperty(
        type=compiled_types.LexicalEnvType, private=True, runtime_check=True,
        has_implicit_env=True,
        doc="""
        Returns the lexical environment that is the scope in which the
        entity designated by this name is defined/used.
        """
    )

    relative_name = AbstractProperty(
        type=compiled_types.Token, private=True, runtime_check=True,
        doc="""
        Returns the relative name of this instance. For example,
        for a prefix A.B.C, this will return C.
        """
    )

    env_elements = Property(Self.env_elements_impl(Env), has_implicit_env=True)

    @langkit_property(private=True,
                      return_type=T.root_node.env_el().array_type(),
                      kind=AbstractKind.abstract_runtime_check,
                      has_implicit_env=True)
    def env_elements_impl(origin_env=LexicalEnvType):
        """
        Returns the list of annotated elements in the lexical environment
        that can statically be a match for expr before overloading analysis.
        """
        pass

    entities = Property(
        Self.env_elements.map(lambda e: e.el),
        type=T.root_node.array_type(),
        has_implicit_env=True,
        doc="""
        Same as env_elements, but return bare AdaNode instances rather than
        EnvElement instances.
        """
    )


class ParenExpr(Expr):
    expr = Field(type=T.Expr)

    @langkit_property()
    def xref_equation(origin_env=LexicalEnvType):
        return (
            Self.expr.sub_equation(origin_env)
            & Bind(Self.expr.type_var, Self.type_var)
        )


class Op(T.EnumNode):
    """
    Operation in a binary expression.

    Note that the ARM does not consider "double_dot" ("..") as a binary
    operator, but we process it this way here anyway to keep things simple.
    """
    alternatives = ["and", "or", "or_else", "and_then", "xor", "in",
                    "not_in", "abs", "not", "pow", "mult", "div", "mod",
                    "rem", "plus", "minus", "concat", "eq", "neq", "lt",
                    "lte", "gt", "gte", "double_dot"]

    subprograms = Property(
        lambda: Self.node_env.get(Self.match(
            lambda _=Op.alt_and: '"and"',
            lambda _=Op.alt_or: '"or"',
            lambda _=Op.alt_xor: '"xor"',
            lambda _=Op.alt_abs: '"abs"',
            lambda _=Op.alt_not: '"not"',
            lambda _=Op.alt_pow: '"**"',
            lambda _=Op.alt_mult: '"*"',
            lambda _=Op.alt_div: '"/"',
            lambda _=Op.alt_mod: '"mod"',
            lambda _=Op.alt_rem: '"rem"',
            lambda _=Op.alt_plus: '"+"',
            lambda _=Op.alt_minus: '"-"',
            lambda _=Op.alt_concat: '"&"',
            lambda _=Op.alt_eq: '"="',
            lambda _=Op.alt_neq: '"/="',
            lambda _=Op.alt_lt: '"<"',
            lambda _=Op.alt_lte: '"<="',
            lambda _=Op.alt_gt: '">"',
            lambda _=Op.alt_gte: '">="',
            lambda _: '<<>>',
        )).keep(T.BasicSubpDecl.env_el()),
        doc="""
        Return the subprograms corresponding to this operator accessible in the
        lexical environment.
        """
    )

    ref_var = UserField(type=LogicVarType, is_private=True)


class UnOp(Expr):
    op = Field(type=Op)
    expr = Field(type=T.Expr)


class BinOp(Expr):
    left = Field(type=T.Expr)
    op = Field(type=Op)
    right = Field(type=T.Expr)

    ref_val = Property(Self.op.ref_var.get_value)

    @langkit_property()
    def xref_equation(origin_env=LexicalEnvType):
        subps = Var(Self.op.subprograms)
        return (
            Self.left.sub_equation(origin_env)
            & Self.right.sub_equation(origin_env)
        ) & (subps.logic_any(lambda subp: Let(
            lambda ps=subp.subp_spec.unpacked_formal_params:

            # The subprogram's first argument must match Self's left
            # operand.
            Bind(Self.left.type_var, ps.at(0).spec.type)

            # The subprogram's second argument must match Self's right
            # operand.
            & Bind(Self.right.type_var, ps.at(1).spec.type)

            # The subprogram's return type is the type of Self
            & Bind(Self.type_var, subp.subp_spec.returns.designated_type)

            # The operator references the subprogram
            & Bind(Self.op.ref_var, subp)
        )) | Self.no_overload_equation())

    no_overload_equation = Property(
        Bind(Self.type_var, Self.left.type_var)
        & Bind(Self.type_var, Self.right.type_var),
        private=True, doc="""
        When no subprogram is found for this node's operator, use this property
        to construct the xref equation for this node.
        """
    )


class Relation(BinOp):
    no_overload_equation = Property(
        Bind(Self.left.type_var, Self.right.type_var)
        & Bind(Self.type_var, Self.bool_type)
    )


class MembershipExpr(Expr):
    """
    Represent a membership test (in/not in operators).

    Note that we don't consider them as binary operators since multiple
    expressions on the right hand side are allowed.
    """
    expr = Field(type=T.Expr)
    op = Field(type=Op)
    membership_exprs = Field(type=T.AdaNode.list_type())


class Aggregate(Expr):
    ancestor_expr = Field(type=T.Expr)
    assocs = Field(type=T.AssocList)

    xref_stop_resolution = Property(True)

    @langkit_property()
    def xref_equation(origin_env=LexicalEnvType):
        td = Var(Self.type_val.el.cast(BaseTypeDecl))
        atd = Var(td.array_def)
        return If(
            atd.is_null,

            # First case, aggregate for a record
            td.record_def.components.match_param_list(
                Self.assocs, False
            ).logic_all(
                lambda pm:
                Bind(pm.actual.assoc.expr.type_var,
                     pm.formal.spec.type_expression.designated_type)
                & pm.actual.assoc.expr.sub_equation(origin_env)
                & If(pm.actual.name.is_null,
                     LogicTrue(),
                     Bind(pm.actual.name.ref_var, pm.formal.spec))
            ),

            # Second case, aggregate for an array
            Self.assocs.logic_all(
                lambda assoc:
                assoc.expr.sub_equation(origin_env)
                & Bind(assoc.expr.type_var, atd.comp_type)
            )
        )


@abstract
class Name(Expr):

    scope = Property(
        EmptyEnv, has_implicit_env=True,
        doc="""
        Lexical environment this identifier represents. This is similar to
        designated_env although it handles only cases for child units and it is
        used only during the environment population pass so it does not return
        orphan environments.
        """
    )

    @langkit_property(kind=AbstractKind.abstract_runtime_check, private=True,
                      return_type=LogicVarType)
    def ref_var():
        """
        This property proxies the logic variable that points to the entity that
        this name refers to. For example, for a simple dotted name::

            A.B

        The dotted name's ref var is the one of the SingleTokNode B.
        """
        pass

    ref_val = Property(Self.ref_var.get_value)

    designated_type_impl = AbstractProperty(
        type=BaseTypeDecl, runtime_check=True, has_implicit_env=True,
        doc="""
        Assuming this name designates a type, return this type.

        Since in Ada this can be resolved locally without any non-local
        analysis, this doesn't use logic equations.
        """
    )

    name_designated_type = Property(
        Self.node_env.eval_in_env(Self.designated_type_impl),
        doc="""
        Like SubtypeIndication.designated_type, but on names, since because of
        Ada's ambiguous grammar, some subtype indications will be parsed as
        names.
        """
    )

    @langkit_property(return_type=AnalysisUnitType, external=True)
    def referenced_unit(kind=AnalysisUnitKind):
        """
        Return the analysis unit for the given "kind" corresponding to this
        Name. Return null if this is an illegal unit name.
        """
        pass

    @langkit_property()
    def matches(n=T.Name):
        """
        Return whether two names match each other.

        This compares the symbol for Identifier and StringLiteral nodes. We
        consider that there is no match for all other node kinds.
        """
        return Self.match(
            lambda id=Identifier:
                n.cast(Identifier).then(
                    lambda other_id:
                    id.tok.symbol.equals(other_id.tok.symbol)
                ),
            lambda sl=StringLiteral:
                n.cast(StringLiteral).then(
                    lambda other_sl:
                    sl.tok.symbol.equals(other_sl.tok.symbol)
                ),
            lambda _: False
        )


class CallExpr(Name):
    """
    Represent a syntactic call expression.

    At the semantic level, this can be either a subprogram call, an array
    subcomponent access expression, an array slice or a type conversion.
    """
    name = Field(type=T.Name)
    suffix = Field(type=T.AdaNode)

    ref_var = Property(Self.name.ref_var)

    @langkit_property(has_implicit_env=True)
    def designated_env(origin_env=LexicalEnvType):
        ignore(origin_env)
        return Self.env_elements().map(lambda e: e.match(
            lambda subp=BasicSubpDecl.env_el(): subp.defining_env,
            lambda subp=SubpBody.env_el():      subp.defining_env,
            lambda _:                           EmptyEnv,
        )).env_group

    @langkit_property()
    def env_elements_impl(origin_env=LexicalEnvType):
        return Self.name.env_elements_impl(origin_env)

    # CallExpr can appear in type expressions: they are used to create implicit
    # subtypes for discriminated records or arrays.
    designated_type_impl = Property(Self.name.designated_type_impl)

    params = Property(Self.suffix.cast(T.AssocList))

    @langkit_property(return_type=EquationType)
    def xref_equation(origin_env=LexicalEnvType):
        return If(
            Not(Self.name.designated_type_impl.is_null),

            # Type conversion case
            Self.type_conv_xref_equation(origin_env),

            # General case. We'll call general_xref_equation on the innermost
            # call expression, to handle nested call expression cases.
            Self.innermost_callexpr.general_xref_equation(origin_env)
        )

    @langkit_property(return_type=EquationType, private=True,
                      has_implicit_env=True)
    def type_conv_xref_equation(origin_env=LexicalEnvType):
        """
        Helper for xref_equation, handles construction of the equation in type
        conversion cases.
        """
        return And(
            Self.params.at(0).expr.sub_equation(origin_env),
            Self.name.sub_equation(origin_env),
            Bind(Self.type_var, Self.name.type_var),
            Bind(Self.ref_var, Self.name.ref_var)
        )

    @langkit_property(return_type=EquationType, private=True,
                      has_implicit_env=True)
    def general_xref_equation(origin_env=LexicalEnvType):
        """
        Helper for xref_equation, handles construction of the equation in
        subprogram call cases.
        """
        # List of every applicable subprogram
        subps = Var(Self.env_elements)

        return (
            Self.name.sub_equation(origin_env)
            # TODO: For the moment we presume that a CallExpr in an expression
            # context necessarily has a AssocList as a suffix, but this is not
            # always true (for example, entry families calls). Handle the
            # remaining cases.
            & Self.params.logic_all(
                lambda pa: pa.expr.sub_equation(origin_env)
            )

            # For each potential subprogram match, we want to express the
            # following constraints:
            & subps.logic_any(lambda e: Let(
                lambda s=e.cast(BasicDecl.env_el()):

                # The called entity is the subprogram
                Bind(Self.name.ref_var, e)

                & If(
                    # Test if the entity is a parameterless subprogram call,
                    # or something else (a component/local variable/etc),
                    # that would make this callexpr an array access.
                    s.subp_spec_or_null.then(lambda ss: ss.paramless(e.MD),
                                             default_val=True),

                    Self.equation_for_type(origin_env, s.type_designator),

                    # The type of the expression is the expr_type of the
                    # subprogram.
                    Bind(Self.type_var, s.expr_type)

                    # For each parameter, the type of the expression matches
                    # the expected type for this subprogram.
                    & s.subp_spec_or_null.match_param_list(
                        Self.params, e.MD.dottable_subp
                    ).logic_all(
                        lambda pm: (
                            # The type of each actual matches the type of the
                            # formal.
                            Bind(
                                pm.actual.assoc.expr.type_var,
                                pm.formal.spec.type_expression.designated_type,
                                eq_prop=BaseTypeDecl.fields.matching_type
                            )
                        ) & If(
                            # Bind actuals designators to parameters if there
                            # are designators.
                            pm.actual.name.is_null,
                            LogicTrue(),
                            Bind(pm.actual.name.ref_var, pm.formal.spec)
                        )
                    )
                )
                # For every callexpr between self and the furthest callexpr
                # that is an ancestor of Self via the name chain, we'll
                # construct the crossref equation.
                & Self.parent_nested_callexpr.then(
                    lambda pce: pce.parent_callexprs_equation(
                        origin_env,
                        Self.type_component(s.type_designator)
                    ), default_val=LogicTrue()
                )
            ))

            # Bind the callexpr's ref_var to the id's ref var
            & Bind(Self.ref_var, Self.name.ref_var)
        )

    @langkit_property(return_type=EquationType, private=True,
                      has_implicit_env=True)
    def equation_for_type(origin_env=LexicalEnvType, type_designator=AdaNode):
        """
        Construct an equation verifying if Self is conformant to the type
        designator passed in parameter.
        """
        atd = Var(type_designator.match(
            lambda te=TypeExpr: te.array_def,
            lambda td=BaseTypeDecl: td.array_def,
            lambda _: No(ArrayTypeDef)
        ))

        return Let(lambda indices=atd.indices: Self.params.logic_all(
            lambda i, pa:
            pa.expr.sub_equation(origin_env)
            & indices.constrain_index_expr(pa.expr, i)
        )) & Bind(Self.type_var, atd.comp_type)

    @langkit_property(return_type=BoolType)
    def check_type_self(type_designator=AdaNode):
        """
        Internal helper for check_type. Implements the logic for the current
        node only. TODO: Waiting on interfaces.
        """
        # TODO: Interface for type designator would be of course 100* better
        # TODO 2: For the moment this is specialized for arrays, but we need to
        # handle the case when the return value is an access to subprogram.
        return type_designator.match(
            lambda te=TypeExpr: te.array_ndims,
            lambda td=BaseTypeDecl: td.array_ndims,
            lambda _: -1
        ) == Self.suffix.cast_or_raise(AssocList).length

    @langkit_property(return_type=AdaNode)
    def type_component(type_designator=AdaNode):
        """
        Helper to return the type component of a Node that can be either a
        BaseTypeDecl or a TypeExpr. TODO: Waiting on interfaces.
        """
        return type_designator.match(
            lambda te=TypeExpr: te.comp_type,
            lambda td=BaseTypeDecl: td.comp_type,
            lambda _: No(AdaNode)
        )

    @langkit_property(return_type=BoolType)
    def check_type_internal(type_designator=AdaNode):
        """
        Internal helper for check_type. Will call check_type_self on Self and
        all parent CallExprs.
        """
        return And(
            Self.check_type_self(type_designator),
            Self.parent.cast(T.CallExpr).then(
                lambda ce: ce.check_type_internal(
                    Self.type_component(type_designator)
                ), default_val=True
            )
        )

    @langkit_property(return_type=BoolType)
    def check_type(type_designator=AdaNode):
        """
        Verifies that this callexpr is valid for the type designated by
        type_designator. type_designator is either a BaseTypeDecl or a
        TypeExpr. TODO: Waiting on interfaces.
        """
        # Algorithm: We're:
        # 1. Taking the innermost call expression
        # 2. Recursing down call expression and component types up to self,
        # checking for each level that the call expression corresponds.
        return Self.innermost_callexpr.check_type_internal(type_designator)

    @langkit_property(return_type=T.CallExpr)
    def innermost_callexpr():
        """
        Helper property. Will return the innermost call expression following
        the name chain. For, example, given::
            A (B) (C) (D)
            ^-----------^ Self
            ^-------^     Self.name
            ^---^         Self.name.name

        Self.innermost_callexpr will return the node corresponding to
        Self.name.name.
        """
        return Self.name.cast(T.CallExpr).then(
            lambda ce: ce.innermost_callexpr(), default_val=Self
        )

    @langkit_property(return_type=T.CallExpr)
    def parent_nested_callexpr():
        """
        Will return the parent callexpr iff Self is the name of the parent
        callexpr.
        """
        return Self.parent.cast(T.CallExpr).then(
            lambda ce: If(ce.name == Self, ce, No(CallExpr))
        )

    @langkit_property(return_type=EquationType, private=True,
                      has_implicit_env=True)
    def parent_callexprs_equation(origin_env=LexicalEnvType,
                                  designator_type=AdaNode):
        """
        Construct the xref equation for the chain of parent nested callexprs.
        """
        return (
            Self.equation_for_type(origin_env, designator_type)
            & Self.parent_nested_callexpr.then(
                lambda pce: pce.parent_callexprs_equation(
                    origin_env,
                    Self.type_component(designator_type)
                ), default_val=LogicTrue()
            )
        )


@abstract
@has_abstract_list
class BasicAssoc(AdaNode):
    expr = AbstractProperty(type=T.Expr)
    names = AbstractProperty(type=T.AdaNode.array_type())


class ParamAssoc(BasicAssoc):
    """
    Assocation (X => Y) used for aggregates and parameter associations.
    """
    designator = Field(type=T.AdaNode)
    r_expr = Field(type=T.Expr)

    expr = Property(Self.r_expr)
    names = Property(If(Self.designator.is_null,
                        EmptyArray(AdaNode), Self.designator.singleton))


class AggregateAssoc(BasicAssoc):
    """
    Assocation (X => Y) used for aggregates and parameter associations.
    """
    designators = Field(type=T.AdaNode.list_type())
    r_expr = Field(type=T.Expr)

    expr = Property(Self.r_expr)
    names = Property(Self.designators.map(lambda d: d))


class AssocList(BasicAssoc.list_type()):

    @langkit_property()
    def unpacked_params():
        """
        Given the list of ParamAssoc, that can in certain case designate
        several actual parameters at once, create an unpacked list of
        SingleActual instances.
        """
        return Self.mapcat(lambda pa: Let(lambda names=pa.names: If(
            names.length == 0,
            New(SingleActual, name=No(Identifier), assoc=pa).singleton,
            names.filtermap(
                filter_expr=lambda n: n.is_a(T.BaseId),
                expr=lambda i:
                New(SingleActual, name=i.cast(T.BaseId), assoc=pa)
            )
        )))


class ExplicitDeref(Name):
    prefix = Field(type=T.Name)
    ref_var = Property(Self.prefix.ref_var)

    @langkit_property()
    def designated_env(origin_env=LexicalEnvType):
        # Since we have implicit dereference in Ada, everything is directly
        # accessible through the prefix, so we just use the prefix's env.
        return Self.prefix.designated_env(origin_env)

    @langkit_property()
    def env_elements_impl(origin_env=LexicalEnvType):
        return Self.prefix.env_elements_impl(origin_env).filter(
            # Env elements for access derefs need to be of an access type
            lambda e: e.el.cast(BasicDecl)._.canonical_expr_type.is_access_type
        )

    @langkit_property()
    def xref_equation(origin_env=LexicalEnvType):
        return (
            Self.prefix.sub_equation(origin_env)
            # Evaluate the prefix equation

            & Self.ref_var.domain(Self.env_elements)
            # Restrict the domain of the reference to entities that are of an
            # access type.

            & Bind(Self.ref_var, Self.prefix.ref_var)
            # Propagate this constraint upward to the prefix expression

            & Bind(Self.prefix.type_var,
                   Self.type_var,
                   BaseTypeDecl.fields.accessed_type)
            # We don't need to check if the type is an access type, since we
            # already constrained the domain above.
        )


class BoxExpr(Expr):
    pass


class OthersDesignator(AdaNode):
    pass


class IfExpr(Expr):
    cond_expr = Field(type=T.Expr)
    then_expr = Field(type=T.Expr)
    elsif_list = Field(type=T.ElsifExprPart.list_type())
    else_expr = Field(type=T.Expr)

    @langkit_property()
    def xref_equation(origin_env=LexicalEnvType):
        return (
            # Construct sub equations for common sub exprs
            Self.cond_expr.sub_equation(origin_env)
            & Self.then_expr.sub_equation(origin_env)

            & If(
                Not(Self.else_expr.is_null),
                # If there is an else, then construct sub equation
                Self.else_expr.sub_equation(origin_env)
                # And bind the then expr's and the else expr's types
                & Bind(Self.then_expr.type_var, Self.else_expr.type_var),

                # If no else, then the then_expression has type bool
                Bind(Self.then_expr.type_var, Self.bool_type)
            ) & Self.elsif_list.logic_all(lambda elsif: (
                # Build the sub equations for cond and then exprs
                elsif.cond_expr.sub_equation(origin_env)
                & elsif.then_expr.sub_equation(origin_env)

                # The condition is boolean
                & Bind(elsif.cond_expr.type_var, Self.bool_type)

                # The elsif branch then expr has the same type as Self's
                # then_expr.
                & Bind(Self.then_expr.type_var, elsif.then_expr.type_var)
            )) & Bind(Self.cond_expr.type_var, Self.bool_type)
            & Bind(Self.then_expr.type_var, Self.type_var)
        )


class ElsifExprPart(AdaNode):
    cond_expr = Field(type=T.Expr)
    then_expr = Field(type=T.Expr)


class CaseExpr(Expr):
    expr = Field(type=T.Expr)
    cases = Field(type=T.CaseExprAlternative.list_type())

    @langkit_property()
    def xref_equation(origin_env=LexicalEnvType):
        # We solve Self.expr separately because it is not dependent on the rest
        # of the semres.
        a = Var(Self.expr.resolve_symbols)
        ignore(a)

        return Self.cases.logic_all(lambda alt: (
            alt.choices.logic_all(lambda c: c.match(
                # Expression case
                lambda e=T.Expr:
                Bind(e.type_var, Self.expr.type_val)
                & e.sub_equation(origin_env),

                # TODO: Bind other cases: SubtypeIndication and Range
                lambda _: LogicTrue()
            ))

            # Equations for the dependent expressions
            & alt.expr.sub_equation(origin_env)

            # The type of self is the type of each expr. Also, the type of
            # every expr is bound together by the conjunction of this bind for
            # every branch.
            & Bind(Self.type_var, alt.expr.type_var)
        ))


class CaseExprAlternative(Expr):
    choices = Field(type=T.AdaNode.list_type())
    expr = Field(type=T.Expr)


@abstract
class SingleTokNode(Name):
    tok = Field(type=T.Token)
    relative_name = Property(Self.tok)

    r_ref_var = UserField(LogicVarType, is_private=True)
    """
    This field is the logic variable for this node. It is not used directly,
    instead being retrieved via the ref_var property
    """

    ref_var = Property(Self.r_ref_var)


@abstract
class BaseId(SingleTokNode):

    @langkit_property()
    def scope():
        elt = Var(Env.get(Self.tok).at(0))
        return If(
            Not(elt.is_null) & elt.el.is_a(
                T.PackageDecl, T.PackageBody, T.GenericPackageDecl,
                T.GenericSubpDecl
            ),
            elt.el.children_env,
            EmptyEnv
        )

    @langkit_property()
    def designated_env(origin_env=LexicalEnvType):
        return Self.designated_env_impl(origin_env, False)

    @langkit_property(private=True, has_implicit_env=True)
    def designated_env_impl(origin_env=LexicalEnvType, is_parent_pkg=BoolType):
        """
        Decoupled implementation for designated_env, specifically used by
        DottedName when the parent is a library level package.
        """
        ents = Var(Self.env_elements_baseid(origin_env, is_parent_pkg))

        return Let(lambda el=ents.at(0).el: If(
            is_package(el),
            el.cast(BasicDecl).defining_env,
            ents.map(lambda e: e.el.cast(BasicDecl).defining_env).env_group
        ))

    parent_scope = Property(Env)
    relative_name = Property(Self.tok)

    designated_type_impl = Property(
        # We don't use get_sequential here because declaring two types with
        # the same name in the same scope is an error in Ada, except in the
        # case of incomplete types forward declarations, and in that case
        # we want the complete view anyway.
        # TODO: For correct semantics and xref, we still want to implement
        # correct support, so that references to the incomplete type don't
        # reference the complete type. This is low priority but still needs
        # to be done.
        Env.get(Self.tok).at(0).el.cast(BaseTypeDecl)
    )

    @langkit_property(return_type=CallExpr)
    def parent_callexpr():
        """
        If this BaseId is the main symbol qualifying the prefix in a call
        expression, this returns the corresponding CallExpr node. Return null
        otherwise. For example::

            C (12, 15);
            ^ parent_callexpr = <CallExpr>

            A.B.C (12, 15);
                ^ parent_callexpr = <CallExpr>

            A.B.C (12, 15);
              ^ parent_callexpr = null

            C (12, 15);
               ^ parent_callexpr = null
        """
        return Self.parents.take_while(lambda p: Or(
            p.is_a(CallExpr),
            p.is_a(DottedName, BaseId) & p.parent.match(
                lambda pfx=DottedName: pfx.suffix == p,
                lambda ce=CallExpr: ce.name == p,
                lambda _: False
            )
        )).find(lambda p: p.is_a(CallExpr)).cast(CallExpr)

    @langkit_property(has_implicit_env=True)
    def env_elements_impl(origin_env=LexicalEnvType):
        return Self.env_elements_baseid(origin_env, False)

    @langkit_property(private=True, has_implicit_env=True)
    def env_elements_baseid(origin_env=LexicalEnvType, is_parent_pkg=BoolType):
        """
        Decoupled implementation for env_elements_impl, specifically used by
        designated_env when the parent is a library level package.

        :param is_parent_pkg: Whether the origin of the env request is a
            package or not.
        """
        items = Var(Env.get_sequential(Self.tok, recursive=Not(is_parent_pkg)))
        pc = Var(Self.parent_callexpr)

        def matching_subp(params, subp, env_el):
            # Either the subprogram has is matching the CallExpr's parameters
            return subp.subp_spec.is_matching_param_list(
                params, env_el.MD.dottable_subp
                # Or the subprogram is parameterless, and the returned
                # component (s) matches the callexpr (s).
            ) | subp.expr_type.then(lambda et: (
                subp.subp_spec.paramless(env_el.MD)
                & pc.check_type(et)
            ))

        return If(
            pc.is_null,

            # If it is not the main id in a CallExpr: either the name
            # designates something else than a subprogram, either it designates
            # a subprogram that accepts no explicit argument. So filter out
            # other subprograms.
            items.filter(lambda e: (
                # If we're at the visibility checking point (parent is a
                # package and self is not), we want to check whether the
                # requester has visibility over the element.
                If(is_parent_pkg & Not(is_library_package(e.el)),
                   Env.is_visible_from(origin_env),
                   True)

                & e.el.cast_or_raise(BasicDecl).subp_spec_or_null.then(
                    # If there is a subp_spec, check that it corresponds to
                    # a parameterless subprogram.
                    lambda ss: ss.paramless(e.MD),
                    default_val=True
                )
            )),

            # This identifier is the name for a called subprogram or an array.
            # So only keep:
            # * subprograms for which the actuals match;
            # * arrays for which the number of dimensions match.
            pc.suffix.cast(AssocList).then(lambda params: (
                items.filter(lambda e: e.el.match(
                    lambda subp=BasicSubpDecl:
                        matching_subp(params, subp, e),

                    lambda subp=SubpBody:
                        matching_subp(params, subp, e),

                    # Type conversion case
                    lambda _=BaseTypeDecl: params.length == 1,

                    # In the case of ObjectDecls/BasicDecls in general, verify
                    # that the callexpr is valid for the given type designator.
                    lambda o=ObjectDecl: pc.check_type(o.type_expr),
                    lambda b=BasicDecl: pc.check_type(b.expr_type),

                    lambda _: False
                ))
            ), default_val=items)
        )

    @langkit_property()
    def xref_equation(origin_env=LexicalEnvType):
        ignore(origin_env)
        dt = Self.designated_type_impl
        return If(
            Not(dt.is_null),

            # Type conversion case
            Bind(Self.ref_var, dt) & Bind(Self.type_var, dt),

            # Other cases
            Self.ref_var.domain(Self.env_elements)
            & Bind(Self.ref_var, Self.type_var,
                   BasicDecl.fields.canonical_expr_type)
        )


class Identifier(BaseId):
    _repr_name = "Id"


class StringLiteral(BaseId):
    _repr_name = "Str"

    @langkit_property()
    def xref_equation(origin_env=LexicalEnvType):
        ignore(origin_env)
        return Predicate(BaseTypeDecl.fields.is_str_type, Self.type_var)


class EnumLiteralDecl(BasicDecl):
    enum_identifier = Field(type=T.BaseId)

    @langkit_property(return_type=T.BaseTypeDecl)
    def canonical_expr_type():
        return Self.parents.find(
            lambda p: p.is_a(BaseTypeDecl)
        ).cast(BaseTypeDecl)

    defining_names = Property(Self.enum_identifier.cast(T.Name).singleton)

    env_spec = EnvSpec(
        add_to_env=add_to_env(Self.enum_identifier.tok.symbol, Self)
    )


class CharLiteral(BaseId):
    _repr_name = "Chr"

    @langkit_property()
    def xref_equation(origin_env=LexicalEnvType):
        ignore(origin_env)
        return Predicate(BaseTypeDecl.fields.is_char_type, Self.type_var)


@abstract
class NumLiteral(SingleTokNode):
    _repr_name = "Num"


class RealLiteral(NumLiteral):
    _repr_name = "Real"

    @langkit_property()
    def xref_equation(origin_env=LexicalEnvType):
        ignore(origin_env)
        return Predicate(BaseTypeDecl.fields.is_real_type, Self.type_var)


class IntLiteral(NumLiteral):
    _repr_name = "Int"

    @langkit_property()
    def xref_equation(origin_env=LexicalEnvType):
        ignore(origin_env)
        return Predicate(BaseTypeDecl.fields.is_int_type, Self.type_var)


class NullLiteral(SingleTokNode):
    _repr_name = "Null"


class SingleFormal(Struct):
    name = Field(type=BaseId)
    spec = Field(type=BaseFormalParamDecl)


class SingleActual(Struct):
    name = Field(type=BaseId)
    assoc = Field(type=T.BasicAssoc)


class ParamMatch(Struct):
    """
    Helper data structure to implement SubpSpec/ParamAssocList matching.

    Each value relates to one ParamAssoc.
    """
    has_matched = Field(type=BoolType, doc="""
        Whether the matched ParamAssoc a ParamSpec.
    """)
    actual = Field(type=SingleActual)
    formal = Field(type=SingleFormal)


class SubpSpec(BaseFormalParamHolder):
    name = Field(type=T.Name)
    params = Field(type=T.ParamSpec.list_type())
    returns = Field(type=T.TypeExpr)

    abstract_formal_params = Property(
        Self.params.map(lambda p: p.cast(BaseFormalParamDecl))
    )

    nb_min_params = Property(
        Self.unpacked_formal_params.filter(
            lambda p: p.spec.is_mandatory
        ).length,
        type=LongType, doc="""
        Return the minimum number of parameters this subprogram can be called
        while still being a legal call.
        """
    )

    nb_max_params = Property(
        Self.unpacked_formal_params.length, type=LongType,
        doc="""
        Return the maximum number of parameters this subprogram can be called
        while still being a legal call.
        """
    )

    @langkit_property(return_type=BoolType, has_implicit_env=True)
    def is_matching_param_list(params=AssocList, is_dottable_subp=BoolType):
        """
        Return whether a AssocList is a match for this SubpSpec, i.e.
        whether the argument count (and designators, if any) match.
        """
        match_list = Var(Self.match_param_list(params, is_dottable_subp))
        nb_max_params = If(is_dottable_subp, Self.nb_max_params - 1,
                           Self.nb_max_params)
        nb_min_params = If(is_dottable_subp, Self.nb_min_params - 1,
                           Self.nb_min_params)

        return And(
            params.length <= nb_max_params,
            match_list.all(lambda m: m.has_matched),
            match_list.filter(
                lambda m: m.formal.spec.is_mandatory
            ).length == nb_min_params,
        )

    @langkit_property(return_type=BoolType, has_implicit_env=True)
    def match_param_assoc(pa=ParamAssoc):
        """
        Return whether some parameter association matches an argument in this
        subprogram specification. Note that this matching disregards types: it
        only considers arity and designators (named parameters).
        """
        # Parameter associations can match only if there is at least one
        # formal in this spec.
        return (Self.nb_max_params > 0) & (
            # Then, all associations with no designator match, as we don't
            # consider types.
            Not(pa.designator.is_null)

            # The ones with a designator match iff the designator is an
            # identifier whose name is present in the list of formals.
            | pa.designator.cast(Identifier).then(
                lambda id: Self.unpacked_formal_params.any(
                    lambda p: p.name.matches(id)
                )
            )
        )

    @langkit_property(return_type=BoolType)
    def match_signature(other=T.SubpSpec):
        """
        Return whether SubpSpec's signature matches Self's.

        Note that the comparison for types isn't just a name comparison: it
        compares the canonical subtype.
        """
        return And(
            # Check that the names are the same
            Self.name.matches(other.name),

            # Check that the return type is the same. Caveat: it's not because
            # we could not find the canonical type that it is null!
            #
            # TODO: simplify this code when SubpSpec provides a kind to
            # distinguish functions and procedures.
            If(other.returns.is_null,
               Self.returns.is_null,
               And(Not(other.returns.is_null),
                   canonical_type_or_null(other.returns)
                   == canonical_type_or_null(Self.returns))),

            # Check that there is the same number of formals and that each
            # formal matches.
            Let(
                lambda
                self_params=Self.unpacked_formal_params,
                other_params=other.unpacked_formal_params:

                And(self_params.length == other_params.length,
                    self_params.all(
                        lambda i, p:
                        And(p.name.matches(other_params.at(i).name),
                            canonical_type_or_null(p.spec.type_expression)
                            == canonical_type_or_null(
                                other_params.at(i)
                                .spec.type_expression)
                            )
                    )))
        )

    @langkit_property(return_type=compiled_types.LexicalEnvType, private=True)
    def defining_env():
        """
        Helper for BasicDecl.defining_env.
        """
        return If(Self.returns.is_null, EmptyEnv, Self.returns.defining_env)

    @langkit_property(return_type=BaseTypeDecl)
    def potential_dottable_type():
        """
        If self meets the criterias for being a subprogram callable via the dot
        notation, return the type of dottable elements.
        """
        return Self.params._.at(0)._.type_expr._.element_type

    @langkit_property(return_type=T.BasicDecl.array_type())
    def dottable_subp():
        """
        Used for environments. Returns either an empty array, or an array
        containg the subprogram declaration for this spec, if self meets the
        criterias for being a dottable subprogram.
        """
        bd = Var(Self.parent.cast_or_raise(BasicDecl))
        return If(
            And(
                Self.nb_max_params > 0,
                Self.potential_dottable_type.then(lambda t: And(
                    # Dot notation only works on tagged types
                    t.is_tagged_type,

                    Or(
                        # Needs to be declared in the same scope as the type
                        t.declarative_scope == bd.declarative_scope,

                        # Or in the private part corresponding to the type's
                        # public part. TODO: This is invalid because it will
                        # make private subprograms visible from the outside.
                        # Fix:
                        #
                        # 1. Add a property that synthetizes a full view node
                        # for a tagged type when there isn't one in the source.
                        #
                        # 2. Add this synthetized full view to the private
                        # part of the package where the tagged type is defined,
                        # if there is one, as part of the tagged type
                        # definition's env spec.
                        #
                        # 3. When computing the private part, if there is a
                        # real in-source full view for the tagged type,
                        # replace the synthetic one.
                        #
                        # 4. Then we can just add the private dottable
                        # subprograms to the private full view.

                        t.declarative_scope == bd.declarative_scope.parent
                        .cast(PackageDecl).then(lambda pd: pd.public_part)
                    )
                ))
            ),
            bd.singleton,
            EmptyArray(T.BasicDecl)
        )

    @langkit_property(return_type=BoolType, private=True)
    def paramless(md=Metadata):
        """
        Utility function. Given a subprogram spec and its associated metadata,
        determine if it can be called without parameters (and hence without a
        callexpr).
        """
        return Or(
            md.dottable_subp & (Self.nb_min_params == 1),
            Self.nb_min_params == 0
        )


class Quantifier(T.EnumNode):
    alternatives = ["all", "some"]


class IterType(T.EnumNode):
    alternatives = ["in", "of"]


@abstract
class LoopSpec(AdaNode):
    pass

    @langkit_property(return_type=EquationType,
                      kind=AbstractKind.abstract_runtime_check)
    def xref_equation(origin_env=LexicalEnvType):
        pass


class ForLoopVarDecl(BasicDecl):
    id = Field(type=T.Identifier)
    id_type = Field(type=T.SubtypeIndication)

    defining_names = Property(Self.id.cast(T.Name).singleton)

    expr_type = Property(If(
        Self.id_type.is_null,

        # The type of a for loop variable does not need to be annotated, it can
        # eventually be infered, which necessitates name resolution on the loop
        # specification. Run resolution if necessary.
        Let(lambda p=If(
            Self.id.type_val.el.is_null,
            Self.parent.parent.cast(T.LoopStmt).resolve_symbols,
            True
        ): If(p, Self.id.type_val.el.cast_or_raise(BaseTypeDecl),
              No(BaseTypeDecl))),

        # If there is a type annotation, just return it
        Self.id_type.designated_type.canonical_type
    ))

    env_spec = EnvSpec(
        add_to_env=add_to_env(Self.id.tok.symbol, Self)
    )


class ForLoopSpec(LoopSpec):
    var_decl = Field(type=T.ForLoopVarDecl)
    loop_type = Field(type=IterType)
    has_reverse = Field(type=Reverse)
    iter_expr = Field(type=T.AdaNode)

    @langkit_property(return_type=EquationType)
    def xref_equation(origin_env=LexicalEnvType):
        int = Var(Self.std_entity('integer'))

        return Self.loop_type.match(

            # This is a for .. in
            lambda _=IterType.alt_in:

            # Let's handle the different possibilities
            Self.iter_expr.match(
                # Anonymous range case: for I in 1 .. 100
                # In that case, the type of everything is Standard.Integer.
                lambda binop=T.BinOp:
                Bind(binop.type_var, int) &
                Bind(binop.left.type_var, int) &
                Bind(binop.right.type_var, int) &
                Bind(Self.var_decl.id.type_var, int),

                # Subtype indication case: the induction variable is of the
                # type.
                lambda t=T.SubtypeIndication:
                Bind(Self.var_decl.id.type_var,
                     t.designated_type.canonical_type),

                # Name case: Either the name is a subtype indication, or an
                # attribute on a subtype indication, in which case the logic is
                # the same as above, either it's an expression that yields an
                # iterator.
                lambda t=T.Name: t.name_designated_type.then(
                    lambda typ:
                    Bind(Self.var_decl.id.type_var, typ.canonical_type),
                    # TODO: Handle the iterator case
                    default_val=LogicTrue()
                ),

                lambda _: LogicTrue()  # should never happen
            ),

            # This is a for .. of
            lambda _=IterType.alt_of:
            # Equation for the expression
            Self.iter_expr.sub_equation(origin_env)

            # Then we want the type of the induction variable to be the
            # component type of the type of the expression.
            & Bind(Self.iter_expr.cast(T.Expr).type_var,
                   Self.var_decl.id.type_var,
                   BaseTypeDecl.fields.comp_type)

            # If there is a type annotation, then the type of var should be
            # conformant.
            & If(Self.var_decl.id_type.is_null,
                 LogicTrue(),
                 Bind(Self.var_decl.id.type_var,
                      Self.var_decl.id_type
                      .designated_type.canonical_type))

            # Finally, we want the type of the expression to be an iterable
            # type.
            & Predicate(BaseTypeDecl.fields.is_iterable_type,
                        Self.iter_expr.cast(T.Expr).type_var)
        )


class QuantifiedExpr(Expr):
    quantifier = Field(type=Quantifier)
    loop_spec = Field(type=T.ForLoopSpec)
    expr = Field(type=T.Expr)


class Allocator(Expr):
    subpool = Field(type=T.Expr)
    type_or_expr = Field(type=T.AdaNode)

    @langkit_property()
    def get_allocated_type():
        return Self.type_or_expr.match(
            lambda t=SubtypeIndication: t.designated_type,
            lambda q=QualExpr: q.designated_type,
            lambda _: No(BaseTypeDecl)
        )

    @langkit_property(return_type=EquationType)
    def xref_equation(origin_env=LexicalEnvType):
        return (
            Self.type_or_expr.sub_equation(origin_env)
            & Predicate(BaseTypeDecl.fields.matching_allocator_type,
                        Self.type_var, Self.get_allocated_type)
        )


class QualExpr(Name):
    prefix = Field(type=T.Name)
    suffix = Field(type=T.Expr)

    ref_var = Property(Self.prefix.ref_var)

    @langkit_property(return_type=EquationType)
    def xref_equation(origin_env=LexicalEnvType):
        typ = Self.prefix.designated_type_impl.canonical_type

        return (
            Self.suffix.sub_equation(origin_env)
            & Bind(Self.prefix.ref_var, typ)
            & Bind(Self.prefix.type_var, typ)
            & Bind(Self.suffix.type_var, typ)
            & Bind(Self.type_var, typ)
        )

    # TODO: once we manage to  turn prefix into a subtype indication, remove
    # this property and update Allocator.get_allocated type to do:
    # q.prefix.designated_type.
    designated_type = Property(
        Self.node_env.eval_in_env(Self.designated_type_impl),
    )
    designated_type_impl = Property(Self.prefix.designated_type_impl)


class AttributeRef(Name):
    prefix = Field(type=T.Name)
    attribute = Field(type=T.Identifier)
    args = Field(type=T.AdaNode)

    ref_var = Property(Self.prefix.ref_var)

    designated_type_impl = Property(
        If(Self.attribute.tok.symbol == 'Class',
           Self.prefix.designated_type_impl.classwide_type,
           Self.prefix.designated_type_impl)
    )


class RaiseExpr(Expr):
    exception_name = Field(type=T.Expr)
    error_message = Field(type=T.Expr)


class DottedName(Name):
    prefix = Field(type=T.Name)
    suffix = Field(type=T.BaseId)
    ref_var = Property(Self.suffix.ref_var)

    @langkit_property()
    def designated_env(origin_env=LexicalEnvType):
        pfx_env = Var(Self.prefix.designated_env(origin_env))
        return pfx_env.eval_in_env(If(
            is_library_package(pfx_env.env_node) & Self.suffix.is_a(T.BaseId),
            Self.suffix.designated_env_impl(origin_env, True),
            Self.suffix.designated_env(origin_env)
        ))

    scope = Property(Self.suffix.then(
        lambda sfx: Self.parent_scope.eval_in_env(sfx.scope),
        default_val=EmptyEnv
    ))

    parent_scope = Property(Self.prefix.scope)

    relative_name = Property(Self.suffix.relative_name)

    @langkit_property()
    def env_elements_impl(origin_env=LexicalEnvType):
        pfx_env = Var(Self.prefix.designated_env(origin_env))

        return pfx_env.eval_in_env(If(
            is_library_package(pfx_env.env_node) & Self.suffix.is_a(T.BaseId),
            Self.suffix.env_elements_baseid(origin_env, True),
            Self.suffix.env_elements_impl(origin_env)
        ))

    designated_type_impl = Property(lambda: (
        Self.prefix.env_elements.at(0).children_env.eval_in_env(
            Self.suffix.designated_type_impl
        )
    ))

    @langkit_property()
    def xref_equation(origin_env=LexicalEnvType):
        dt = Self.designated_type_impl
        base = Var(
            Self.prefix.sub_equation(origin_env)
            & Self.prefix.designated_env(origin_env).eval_in_env(
                Self.suffix.sub_equation(origin_env)
            )
        )
        return If(
            Not(dt.is_null),
            base,
            base & Self.env_elements.logic_any(lambda e: (
                Bind(Self.suffix.ref_var, e)
                & e.cast(BasicDecl.env_el()).constrain_prefix(Self.prefix)
                & Bind(Self.type_var, Self.suffix.type_var)
            ))
        )


class CompilationUnit(AdaNode):
    """Root node for all Ada analysis units."""
    prelude = Field(doc="``with``, ``use`` or ``pragma`` statements.")
    body = Field(type=T.AdaNode)
    pragmas = Field(type=T.Pragma.list_type())

    env_spec = EnvSpec(
        env_hook_arg=Self,
        ref_envs=Self.node_env.get('Standard').at(0).then(
            lambda std: std.children_env.singleton,
            default_val=EmptyArray(LexicalEnvType)
        )
    )


class SubpBody(Body):
    env_spec = child_unit(
        '__body',
        If(is_library_item(Self),
           Let(lambda scope=Self.subp_spec.name.scope:
               If(scope == EmptyEnv,
                  Self.subp_spec.name.parent_scope,
                  scope)),
           Self.parent.children_env)
    )

    overriding = Field(type=Overriding)
    subp_spec = Field(type=T.SubpSpec)
    aspects = Field(type=T.AspectSpec)
    decls = Field(type=T.DeclarativePart)
    stmts = Field(type=T.HandledStmts)
    end_id = Field(type=T.Name)

    defining_names = Property(Self.subp_spec.name.singleton)
    defining_env = Property(Self.subp_spec.defining_env)

    decl_part = Property(
        If(
            is_library_item(Self),

            get_library_item(Self.spec_unit).match(
                lambda subp_decl=T.SubpDecl: subp_decl,
                lambda gen_subp_decl=T.GenericSubpDecl: gen_subp_decl,
                lambda _: No(T.AdaNode)
            ),

            decl_scope_decls(Self).filter(
                lambda decl:
                Let(lambda
                    spec=decl.match(
                        lambda subp_decl=T.SubpDecl: subp_decl.subp_spec,
                        lambda gen_subp_decl=T.GenericSubpDecl:
                            gen_subp_decl.subp_spec,
                        lambda _: No(T.SubpSpec)
                    ):

                    spec._.match_signature(Self.subp_spec))
            ).at(0)),
        doc="""
        Return the SubpDecl corresponding to this node.
        """
    )


class HandledStmts(AdaNode):
    stmts = Field(type=T.AdaNode.list_type())
    exceptions = Field(type=T.AdaNode.list_type())


class ExceptionHandler(AdaNode):
    exc_name = Field(type=T.Identifier)
    handled_exceptions = Field(type=T.AdaNode.list_type())
    stmts = Field(type=T.AdaNode.list_type())


@abstract
class Stmt(AdaNode):
    xref_entry_point = Property(True)


@abstract
class SimpleStmt(Stmt):
    pass


@abstract
class CompositeStmt(Stmt):
    pass


class CallStmt(SimpleStmt):
    """
    Statement for entry or procedure calls.
    """
    call = Field(type=T.Expr)

    @langkit_property()
    def xref_equation(origin_env=LexicalEnvType):
        return (
            Self.call.sub_equation(origin_env)

            # Call statements can have no return value
            & Bind(Self.call.type_var, No(AdaNode))
        )


class NullStmt(SimpleStmt):
    null_lit = Field(repr=False)

    @langkit_property()
    def xref_equation(origin_env=LexicalEnvType):
        ignore(origin_env)
        return LogicTrue()


class AssignStmt(SimpleStmt):
    dest = Field(type=T.Expr)
    expr = Field(type=T.Expr)

    @langkit_property()
    def xref_equation(origin_env=LexicalEnvType):
        return (
            Self.dest.sub_equation(origin_env)
            & Self.expr.sub_equation(origin_env)
            & Bind(Self.expr.type_var, Self.dest.type_var,
                   eq_prop=BaseTypeDecl.fields.matching_type)
        )


class GotoStmt(SimpleStmt):
    label_name = Field(type=T.Name)

    @langkit_property()
    def xref_equation(origin_env=LexicalEnvType):
        return Self.label_name.sub_equation(origin_env)


class ExitStmt(SimpleStmt):
    loop_name = Field(type=T.Identifier)
    condition = Field(type=T.Expr)

    @langkit_property()
    def xref_equation(origin_env=LexicalEnvType):
        return (
            Self.condition.sub_equation(origin_env)
            & Bind(Self.condition.type_var, Self.bool_type)
        )


class ReturnStmt(SimpleStmt):
    return_expr = Field(type=T.Expr)

    subp = Property(
        Self.parents.find(lambda p: p.is_a(SubpBody)).cast(SubpBody),
        doc="Returns the subprogram this return statement belongs to"
    )

    @langkit_property()
    def xref_equation(origin_env=LexicalEnvType):
        return (
            Self.return_expr.sub_equation(origin_env)
            & Bind(
                Self.return_expr.type_var,
                Self.subp.subp_spec.returns.designated_type.canonical_type
            )
        )


class RequeueStmt(SimpleStmt):
    call_name = Field(type=T.Expr)
    has_abort = Field(type=Abort)


class AbortStmt(SimpleStmt):
    names = Field(type=T.Name.list_type())


class DelayStmt(SimpleStmt):
    has_until = Field(type=Until)
    expr = Field(type=T.Expr)

    @langkit_property()
    def xref_equation(origin_env=LexicalEnvType):
        return Self.expr.sub_equation(origin_env) & Bind(
            Self.expr.type_var, Self.std_entity('Duration')
        )


class RaiseStmt(SimpleStmt):
    exception_name = Field(type=T.Expr)
    error_message = Field(type=T.Expr)

    @langkit_property()
    def xref_equation(origin_env=LexicalEnvType):
        return Self.exception_name.sub_equation(origin_env)


class IfStmt(CompositeStmt):
    condition = Field(type=T.Expr)
    stmts = Field(type=T.AdaNode.list_type())
    alternatives = Field(type=T.ElsifStmtPart.list_type())
    else_stmts = Field(type=T.AdaNode.list_type())

    @langkit_property()
    def xref_equation(origin_env=LexicalEnvType):
        return (
            Self.condition.sub_equation(origin_env)
            & Bind(Self.condition.type_var, Self.bool_type)
            & Self.alternatives.logic_all(
                lambda elsif: elsif.expr.sub_equation(origin_env)
                & Bind(elsif.expr.type_var, Self.bool_type)
            )
        )


class ElsifStmtPart(AdaNode):
    expr = Field(type=T.Expr)
    stmts = Field(type=T.AdaNode.list_type())


class LabelDecl(BasicDecl):
    name = Field(type=T.Identifier)
    env_spec = EnvSpec(add_to_env=add_to_env(Self.name.tok.symbol, Self))
    defining_names = Property(Self.name.cast(T.Name).singleton)


class Label(SimpleStmt):
    decl = Field(type=T.LabelDecl)


class WhileLoopSpec(LoopSpec):
    expr = Field(type=T.Expr)

    @langkit_property(return_type=EquationType)
    def xref_equation(origin_env=LexicalEnvType):
        return Self.expr.sub_equation(origin_env) & (
            Bind(Self.expr.type_var, Self.bool_type)
        )


class NamedStmtDecl(BasicDecl):
    """
    BasicDecl that is always the declaration inside a named statement.
    """
    name = Field(type=T.Identifier)
    defining_names = Property(Self.name.cast(T.Name).singleton)
    defining_env = Property(Self.parent.cast(T.NamedStmt).stmt.children_env)


class NamedStmt(CompositeStmt):
    """
    Wrapper class, used for composite statements that can be named (declare
    blocks, loops). This allows to both have a BasicDecl for the named entity
    declared, and a CompositeStmt for the statement hierarchy.
    """
    decl = Field(type=T.NamedStmtDecl)
    stmt = Field(type=T.CompositeStmt)

    env_spec = EnvSpec(
        add_env=True,
        add_to_env=add_to_env(Self.decl.name.tok.symbol, Self.decl)
    )


class LoopStmt(CompositeStmt):
    spec = Field(type=T.LoopSpec)
    stmts = Field(type=T.AdaNode.list_type())
    end_id = Field(type=T.Identifier)

    @langkit_property(return_type=EquationType)
    def xref_equation(origin_env=LexicalEnvType):
        return Self.spec.xref_equation(origin_env)


class BlockStmt(CompositeStmt):
    decls = Field(type=T.DeclarativePart)
    stmts = Field(type=T.HandledStmts)
    end_id = Field(type=T.Identifier)

    env_spec = EnvSpec(add_env=True)


class ExtendedReturnStmt(CompositeStmt):
    object_decl = Field(type=T.ObjectDecl)
    stmts = Field(type=T.HandledStmts)

    @langkit_property(return_type=EquationType)
    def xref_equation(origin_env=LexicalEnvType):
        ignore(origin_env)
        return LogicTrue()

    env_spec = EnvSpec(add_env=True)


class CaseStmt(CompositeStmt):
    case_expr = Field(type=T.Expr)
    case_alts = Field(type=T.CaseStmtAlternative.list_type())


class CaseStmtAlternative(AdaNode):
    choices = Field(type=T.AdaNode.list_type())
    stmts = Field(type=T.AdaNode.list_type())


class AcceptStmt(CompositeStmt):
    name = Field(type=T.Identifier)
    entry_index_expr = Field(type=T.Expr)
    params = Field(type=T.ParamSpec.list_type())
    stmts = Field(type=T.HandledStmts)


class SelectStmt(CompositeStmt):
    guards = Field(type=T.SelectWhenPart.list_type())
    else_stmts = Field(type=T.AdaNode.list_type())
    abort_stmts = Field(type=T.AdaNode.list_type())


class SelectWhenPart(AdaNode):
    choices = Field(type=T.Expr)
    stmts = Field(type=T.AdaNode.list_type())


class TerminateAlternative(SimpleStmt):
    pass


class PackageBody(Body):
    env_spec = child_unit('__body', Self.package_name.scope)

    package_name = Field(type=T.Name)
    aspects = Field(type=T.AspectSpec)
    decls = Field(type=T.DeclarativePart)
    stmts = Field(type=T.HandledStmts)
    end_id = Field(type=T.Name)

    defining_names = Property(Self.package_name.singleton)
    defining_env = Property(Self.children_env.env_orphan)

    @langkit_property(return_type=T.BasePackageDecl)
    def decl_part():
        """
        Return the BasePackageDecl corresponding to this node.

        If the case of generic package declarations, this returns the
        "package_decl" field instead of the GenericPackageDecl itself.
        """
        return Self.parent.node_env.eval_in_env(
            Self.package_name.entities.at(0).match(
                lambda pkg_decl=T.PackageDecl: pkg_decl,
                lambda gen_pkg_decl=T.GenericPackageDecl:
                    gen_pkg_decl.package_decl,
                lambda _: No(T.BasePackageDecl)
            )
        )


class TaskBody(Body):
    name = Field(type=T.Name)
    aspects = Field(type=T.AspectSpec)
    decls = Field(type=T.DeclarativePart)
    stmts = Field(type=T.HandledStmts)

    defining_names = Property(Self.name.singleton)


class ProtectedBody(Body):
    name = Field(type=T.Name)
    aspects = Field(type=T.AspectSpec)
    decls = Field(type=T.DeclarativePart)

    defining_names = Property(Self.name.singleton)


class EntryBody(Body):
    entry_name = Field(type=T.Identifier)
    index_spec = Field(type=T.EntryIndexSpec)
    params = Field(type=T.ParamSpec.list_type())
    barrier = Field(type=T.Expr)
    decls = Field(type=T.DeclarativePart)
    stmts = Field(type=T.HandledStmts)

    defining_names = Property(Self.entry_name.cast(Name).singleton)


class EntryIndexSpec(AdaNode):
    id = Field(type=T.Identifier)
    subtype = Field(type=T.AdaNode)


class Subunit(AdaNode):
    name = Field(type=T.Name)
    body = Field(type=T.Body)


class ProtectedBodyStub(BodyStub):
    name = Field(type=T.Name)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Self.name.singleton)


class SubpBodyStub(BodyStub):
    overriding = Field(type=Overriding)
    subp_spec = Field(type=T.SubpSpec)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Self.subp_spec.name.singleton)
    # Note that we don't have to override the defining_env property here since
    # what we put in lexical environment is their SubpSpec child.


class PackageBodyStub(BodyStub):
    name = Field(type=T.Name)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Self.name.singleton)


class TaskBodyStub(BodyStub):
    name = Field(type=T.Name)
    aspects = Field(type=T.AspectSpec)

    defining_names = Property(Self.name.singleton)


class LibraryItem(AdaNode):
    has_private = Field(type=Private)
    item = Field(type=T.BasicDecl)
