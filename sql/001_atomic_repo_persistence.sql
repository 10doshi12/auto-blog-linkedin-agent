ALTER TABLE posts
ADD COLUMN IF NOT EXISTS source_repo_id bigint;

CREATE UNIQUE INDEX IF NOT EXISTS posts_source_repo_id_key
ON posts (source_repo_id)
WHERE source_repo_id IS NOT NULL;

ALTER TABLE projects
ADD COLUMN IF NOT EXISTS source_repo_id bigint;

CREATE UNIQUE INDEX IF NOT EXISTS projects_source_repo_id_key
ON projects (source_repo_id)
WHERE source_repo_id IS NOT NULL;

ALTER TABLE agent_processed_repos
ADD COLUMN IF NOT EXISTS project_id uuid REFERENCES projects(id) ON DELETE SET NULL;

ALTER TABLE agent_processed_repos
DROP CONSTRAINT IF EXISTS agent_processed_repos_status_check;

ALTER TABLE agent_processed_repos
ADD CONSTRAINT agent_processed_repos_status_check
CHECK (status = ANY (ARRAY['in_progress', 'success', 'skipped', 'failed', 'cancelled']));

CREATE OR REPLACE FUNCTION persist_repo_result(
  p_repo_id bigint,
  p_repo_name text,
  p_status text,
  p_skip_reason text DEFAULT NULL,
  p_blog_post jsonb DEFAULT NULL,
  p_project jsonb DEFAULT NULL,
  p_raw_llm_output jsonb DEFAULT NULL,
  p_processed_at timestamptz DEFAULT now()
)
RETURNS TABLE(blog_post_id uuid, project_id uuid)
LANGUAGE plpgsql
AS $$
DECLARE
  v_blog_post_id uuid;
  v_project_id uuid;
BEGIN
  IF p_status = 'success' THEN
    IF (p_blog_post IS NULL) <> (p_project IS NULL) THEN
      RAISE EXCEPTION 'success status requires both blog and project payloads, or neither for audit-only runs';
    END IF;

    IF p_blog_post IS NOT NULL AND p_project IS NOT NULL THEN
      INSERT INTO posts (
      source_repo_id,
      slug,
      title,
      excerpt,
      content,
      tags,
      reading_time_minutes,
      week_number,
      published,
      published_at
    )
    VALUES (
      p_repo_id,
      p_blog_post->>'slug',
      p_blog_post->>'title',
      p_blog_post->>'excerpt',
      p_blog_post->>'content',
      ARRAY(SELECT jsonb_array_elements_text(COALESCE(p_blog_post->'tags', '[]'::jsonb))),
      (p_blog_post->>'reading_time_minutes')::integer,
      NULLIF(p_blog_post->>'week_number', '')::integer,
      (p_blog_post->>'published')::boolean,
      NULLIF(p_blog_post->>'published_at', '')::timestamptz
    )
      ON CONFLICT (source_repo_id) DO UPDATE
      SET
        slug = EXCLUDED.slug,
        title = EXCLUDED.title,
        excerpt = EXCLUDED.excerpt,
        content = EXCLUDED.content,
        tags = EXCLUDED.tags,
        reading_time_minutes = EXCLUDED.reading_time_minutes,
        week_number = EXCLUDED.week_number,
        published = EXCLUDED.published,
        published_at = EXCLUDED.published_at
      RETURNING id INTO v_blog_post_id;

      INSERT INTO projects (
      source_repo_id,
      slug,
      title,
      description,
      content,
      category,
      tags,
      metric,
      github_url,
      live_url,
      has_detail_page,
      featured,
      display_order
    )
    VALUES (
      p_repo_id,
      p_project->>'slug',
      p_project->>'title',
      p_project->>'description',
      NULLIF(p_project->>'content', ''),
      p_project->>'category',
      ARRAY(SELECT jsonb_array_elements_text(COALESCE(p_project->'tags', '[]'::jsonb))),
      NULLIF(p_project->>'metric', ''),
      NULLIF(p_project->>'github_url', ''),
      NULLIF(p_project->>'live_url', ''),
      (p_project->>'has_detail_page')::boolean,
      (p_project->>'featured')::boolean,
      (p_project->>'display_order')::integer
    )
      ON CONFLICT (source_repo_id) DO UPDATE
      SET
        slug = EXCLUDED.slug,
        title = EXCLUDED.title,
        description = EXCLUDED.description,
        content = EXCLUDED.content,
        category = EXCLUDED.category,
        tags = EXCLUDED.tags,
        metric = EXCLUDED.metric,
        github_url = EXCLUDED.github_url,
        live_url = EXCLUDED.live_url,
        has_detail_page = EXCLUDED.has_detail_page,
        featured = EXCLUDED.featured,
        display_order = EXCLUDED.display_order
      RETURNING id INTO v_project_id;
    END IF;
  END IF;

  INSERT INTO agent_processed_repos (
    repo_id,
    repo_name,
    status,
    skip_reason,
    blog_post_id,
    project_id,
    processed_at,
    raw_llm_output
  )
  VALUES (
    p_repo_id,
    p_repo_name,
    p_status,
    p_skip_reason,
    v_blog_post_id,
    v_project_id,
    p_processed_at,
    p_raw_llm_output
  )
  ON CONFLICT (repo_id) DO UPDATE
  SET
    repo_name = EXCLUDED.repo_name,
    status = EXCLUDED.status,
    skip_reason = EXCLUDED.skip_reason,
    blog_post_id = COALESCE(EXCLUDED.blog_post_id, agent_processed_repos.blog_post_id),
    project_id = COALESCE(EXCLUDED.project_id, agent_processed_repos.project_id),
    processed_at = EXCLUDED.processed_at,
    raw_llm_output = COALESCE(EXCLUDED.raw_llm_output, agent_processed_repos.raw_llm_output);

  RETURN QUERY SELECT v_blog_post_id, v_project_id;
END;
$$;
