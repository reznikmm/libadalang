procedure Foo is
begin
---------------
-- Undefined --
---------------

#if Undef_Sym = "a" then
   null;
#end if;

-------------
-- Int_Sym --
-------------

#if Int_Sym = "a" then
   null;
#end if;

#if Int_Sym = Int_Sym then
   null;
#end if;

#if Int_Sym = Other_Int_Sym then
   null;
#end if;

#if Int_Sym = Other_Str_Sym then
   null;
#end if;

#if Int_Sym = 1 then
   null;
#end if;

#if Int_Sym = 2 then
   null;
#end if;

-------------
-- Str_Sym --
-------------

#if Str_Sym = """hello""" then
   null;
#end if;

#if Str_Sym = """world""" then
   null;
#end if;

#if Str_Sym = Str_Sym then
   null;
#end if;

#if Str_Sym = Other_Int_Sym then
   null;
#end if;

#if Str_Sym = Other_Str_Sym then
   null;
#end if;

#if Str_Sym = 1 then
   null;
#end if;

-------------
-- Sym_Sym --
-------------

#if Sym_Sym = "A" then
   null;
#end if;

#if Sym_Sym = "B" then
   null;
#end if;

#if Sym_Sym = Sym_Sym then
   null;
#end if;

#if Sym_Sym = Other_Int_Sym then
   null;
#end if;

#if Sym_Sym = Other_Sym_Sym then
   null;
#end if;

#if Sym_Sym = 1 then
   null;
#end if;

end Foo;
