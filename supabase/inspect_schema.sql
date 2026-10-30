-- READ-ONLY. Run in the Supabase SQL editor to see the real schema of
-- public.memories, its constraints, RLS policies and the RPC source.
-- This repository had no migration history, so this is how to confirm
-- the existing design. Save the output as docs/schema-baseline.md if you like.

-- 1. Columns
select column_name, data_type, is_nullable, column_default, is_generated, generation_expression
from information_schema.columns
where table_schema = 'public' and table_name = 'memories'
order by ordinal_position;

-- 2. Constraints and indexes
select conname, pg_get_constraintdef(oid) as definition
from pg_constraint
where conrelid = 'public.memories'::regclass;

select indexname, indexdef from pg_indexes
where schemaname = 'public' and tablename = 'memories';

-- 3. Row-level security
select relname, relrowsecurity as rls_enabled, relforcerowsecurity as rls_forced
from pg_class where oid = 'public.memories'::regclass;

select policyname, cmd, roles, qual, with_check
from pg_policies
where schemaname = 'public' and tablename = 'memories';

-- 4. The set_current_memory function
select p.proname, pg_get_function_arguments(p.oid) as arguments,
       p.prosecdef as security_definer, pg_get_functiondef(p.oid) as source
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and p.proname = 'set_current_memory';
