from langkit.compiled_types import (
    abstract, ASTNode, root_grammar_class, NodeMacro,
    env_metadata, UserField, BoolType, Struct
)
from langkit.envs import EnvSpec
from langkit.expressions import Property, Self
from langkit.parsers import Grammar

from language.parser.lexer import ada_lexer

ada_grammar = Grammar()
A = ada_grammar
ada_grammar.main_rule_name = "compilation_unit"


@env_metadata
class Metadata(Struct):
    dottable_subprogram = UserField(
        BoolType, doc="Wether the stored element is a subprogram accessed "
                      "through the dot notation"
    )
    implicit_deref = UserField(
        BoolType, doc="Wether the stored element is accessed through an "
                      "implicit dereference"
    )


@abstract
@root_grammar_class
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
    pass


class ChildUnit(NodeMacro):
    """
    This macro will add the properties and the env specification necessary to
    make a node implement the specification of a library child unit in Ada, so
    that you can declare new childs to an unit outside of its own scope.

    Requirements::
        name: Property(type=BaseName)
    """

    scope = Property(
        Self.name.scope, private=True,
        doc="""
        Helper property, that will return the scope of definition of this
        child unit.
        """
    )
    env_spec = EnvSpec(
        initial_env=Self.scope, add_env=True, add_to_env=(Self.name, Self)
    )


def eval_grammar():
    # Import all the modules in which the grammar rules are defined, and then
    # delete the module. This way we know that we only import them for side
    # effects: the grammar is extended by every imported module.
    from language.parser import A

    import language.parser.bodies
    import language.parser.decl
    import language.parser.exprs
    import language.parser.types

    del language
    return A

ada_grammar = eval_grammar()
