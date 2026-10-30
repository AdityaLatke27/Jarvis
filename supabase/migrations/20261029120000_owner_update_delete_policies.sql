-- Migration: allow a signed-in user to UPDATE and DELETE their OWN memories.
--
-- Needed by the new correct_memory and delete_memory tools. Only needed if
-- your project does not already have UPDATE/DELETE policies on
-- public.memories (check with supabase/inspect_schema.sql first).
--
-- Safety:
-- * Does not disable, force, or otherwise change row-level security.
-- * Does not touch existing policies, columns, data, or set_current_memory.
-- * Each policy is created only if no policy for that command (or ALL) exists.
-- * Policies are restricted to the `authenticated` role and to rows where
--   user_id = auth.uid(), so one user can never change another user's rows.
-- * Idempotent: running it twice changes nothing.

do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'memories' and cmd in ('UPDATE', 'ALL')
  ) then
    create policy "memories_update_own"
      on public.memories
      for update
      to authenticated
      using ((select auth.uid()) = user_id)
      with check ((select auth.uid()) = user_id);
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'memories' and cmd in ('DELETE', 'ALL')
  ) then
    create policy "memories_delete_own"
      on public.memories
      for delete
      to authenticated
      using ((select auth.uid()) = user_id);
  end if;
end
$$;

-- Rollback (only if this migration created them):
--   drop policy if exists "memories_update_own" on public.memories;
--   drop policy if exists "memories_delete_own" on public.memories;
