type Expr (Kind : Expr_Kind_Type) is record
      
      case Kind is
         when Bin_Op_A | Bin_Op_B =>
            L, R : Expr_Access;
         when Num =>
            Val : Integer;
      end case;
end record;
