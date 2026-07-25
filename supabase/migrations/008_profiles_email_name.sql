-- Add email column to profiles
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS email text;

-- Update trigger to capture name + email from auth metadata at signup time
CREATE OR REPLACE FUNCTION handle_new_user()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = public
AS $$
BEGIN
  INSERT INTO public.profiles (id, name, email)
  VALUES (
    NEW.id,
    NEW.raw_user_meta_data->>'name',
    NEW.email
  )
  ON CONFLICT (id) DO UPDATE
    SET
      name  = COALESCE(profiles.name,  EXCLUDED.name),
      email = COALESCE(profiles.email, EXCLUDED.email);
  RETURN NEW;
END;
$$;
