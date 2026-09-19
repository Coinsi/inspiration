-- Frozen schema for revision 0001. Later revisions own their additions.
CREATE TABLE audit_log (
	id UUID NOT NULL,
	project_id UUID,
	user_id UUID,
	action VARCHAR(64) NOT NULL,
	target_type VARCHAR(64),
	target_id UUID,
	detail JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id)
)
-- statement
CREATE INDEX ix_audit_log_project_id ON audit_log (project_id)
-- statement
CREATE TABLE blob (
	hash VARCHAR(128) NOT NULL,
	storage_uri VARCHAR(512) NOT NULL,
	mime VARCHAR(128) NOT NULL,
	size_bytes BIGINT NOT NULL,
	width INTEGER,
	height INTEGER,
	duration_ms INTEGER,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (hash)
)
-- statement
CREATE TABLE "user" (
	id UUID NOT NULL,
	username VARCHAR(64) NOT NULL,
	email VARCHAR(255) NOT NULL,
	password_hash VARCHAR(255) NOT NULL,
	display_name VARCHAR(128) NOT NULL,
	is_active BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (email)
)
-- statement
CREATE UNIQUE INDEX ix_user_username ON "user" (username)
-- statement
CREATE TABLE project (
	id UUID NOT NULL,
	name VARCHAR(255) NOT NULL,
	description TEXT,
	owner_id UUID NOT NULL,
	settings JSONB NOT NULL,
	code VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	deleted_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_project_code UNIQUE (code),
	FOREIGN KEY(owner_id) REFERENCES "user" (id)
)
-- statement
CREATE TABLE asset (
	id UUID NOT NULL,
	project_id UUID NOT NULL,
	type VARCHAR(16) NOT NULL,
	name VARCHAR(255) NOT NULL,
	summary TEXT,
	status VARCHAR(16) NOT NULL,
	current_version_id UUID,
	representative_blob_hash VARCHAR(128),
	metadata JSONB NOT NULL,
	tags VARCHAR[] NOT NULL,
	scope VARCHAR(16) NOT NULL,
	created_by UUID,
	code VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	deleted_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	FOREIGN KEY(representative_blob_hash) REFERENCES blob (hash),
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE
)
-- statement
CREATE INDEX ix_asset_project_id ON asset (project_id)
-- statement
CREATE INDEX ix_asset_project_type ON asset (project_id, type)
-- statement
CREATE TABLE baseline (
	id UUID NOT NULL,
	project_id UUID NOT NULL,
	name VARCHAR(128) NOT NULL,
	kind VARCHAR(16) NOT NULL,
	note VARCHAR(512),
	created_by UUID,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE
)
-- statement
CREATE INDEX ix_baseline_project_id ON baseline (project_id)
-- statement
CREATE TABLE code_sequence (
	project_id UUID NOT NULL,
	entity_type VARCHAR(64) NOT NULL,
	next_seq BIGINT NOT NULL,
	PRIMARY KEY (project_id, entity_type),
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE
)
-- statement
CREATE TABLE generation_job (
	id UUID NOT NULL,
	project_id UUID NOT NULL,
	target_type VARCHAR(16) NOT NULL,
	target_id UUID NOT NULL,
	provider VARCHAR(64) NOT NULL,
	request_type VARCHAR(16) NOT NULL,
	status VARCHAR(16) NOT NULL,
	external_job_id VARCHAR(255),
	priority INTEGER NOT NULL,
	params JSONB NOT NULL,
	input_snapshot JSONB NOT NULL,
	estimated_cost NUMERIC(14, 2),
	actual_cost NUMERIC(14, 2),
	cost_raw JSONB,
	error TEXT,
	created_by UUID,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE
)
-- statement
CREATE INDEX ix_generation_job_project_id ON generation_job (project_id)
-- statement
CREATE INDEX ix_job_project_status ON generation_job (project_id, status)
-- statement
CREATE INDEX ix_job_sched ON generation_job (status, priority)
-- statement
CREATE TABLE membership (
	id UUID NOT NULL,
	project_id UUID NOT NULL,
	user_id UUID NOT NULL,
	role VARCHAR(16) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES "user" (id) ON DELETE CASCADE,
	CONSTRAINT uq_membership UNIQUE (project_id, user_id),
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE
)
-- statement
CREATE INDEX ix_membership_project_id ON membership (project_id)
-- statement
CREATE TABLE metadata_template (
	id UUID NOT NULL,
	project_id UUID NOT NULL,
	target VARCHAR(32) NOT NULL,
	asset_type VARCHAR(16),
	schema JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE
)
-- statement
CREATE INDEX ix_metadata_template_project_id ON metadata_template (project_id)
-- statement
CREATE TABLE novel (
	id UUID NOT NULL,
	project_id UUID NOT NULL,
	title VARCHAR(255) NOT NULL,
	source_format VARCHAR(32),
	original_blob_hash VARCHAR(128),
	status VARCHAR(16) NOT NULL,
	current_version_id UUID,
	created_by UUID,
	code VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	deleted_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	FOREIGN KEY(original_blob_hash) REFERENCES blob (hash),
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE
)
-- statement
CREATE INDEX ix_novel_project_id ON novel (project_id)
-- statement
CREATE TABLE outline (
	id UUID NOT NULL,
	project_id UUID NOT NULL,
	title VARCHAR(255) NOT NULL,
	content TEXT NOT NULL,
	status VARCHAR(16) NOT NULL,
	current_version_id UUID,
	created_by UUID,
	code VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	deleted_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE
)
-- statement
CREATE INDEX ix_outline_project_id ON outline (project_id)
-- statement
CREATE TABLE prompt (
	id UUID NOT NULL,
	project_id UUID NOT NULL,
	name VARCHAR(255) NOT NULL,
	positive TEXT NOT NULL,
	negative TEXT NOT NULL,
	status VARCHAR(16) NOT NULL,
	current_version_id UUID,
	tags VARCHAR[] NOT NULL,
	created_by UUID,
	code VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	deleted_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE
)
-- statement
CREATE INDEX ix_prompt_project_id ON prompt (project_id)
-- statement
CREATE TABLE prompt_fragment (
	id UUID NOT NULL,
	project_id UUID NOT NULL,
	category VARCHAR(32) NOT NULL,
	name VARCHAR(255) NOT NULL,
	text TEXT NOT NULL,
	created_by UUID,
	code VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	deleted_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE
)
-- statement
CREATE INDEX ix_prompt_fragment_project_id ON prompt_fragment (project_id)
-- statement
CREATE TABLE provider_config (
	id UUID NOT NULL,
	project_id UUID NOT NULL,
	provider_name VARCHAR(64) NOT NULL,
	kind VARCHAR(16) NOT NULL,
	enabled BOOLEAN NOT NULL,
	capabilities JSONB NOT NULL,
	endpoint VARCHAR(512),
	credentials_encrypted BYTEA,
	config JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_provider UNIQUE (project_id, provider_name),
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE
)
-- statement
CREATE INDEX ix_provider_config_project_id ON provider_config (project_id)
-- statement
CREATE TABLE quota (
	id UUID NOT NULL,
	project_id UUID NOT NULL,
	scope VARCHAR(16) NOT NULL,
	user_id UUID,
	limit_cost NUMERIC(14, 2) NOT NULL,
	used_cost NUMERIC(14, 2) NOT NULL,
	period VARCHAR(16) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES "user" (id),
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE,
	CONSTRAINT uq_quota UNIQUE (project_id, scope, user_id)
)
-- statement
CREATE INDEX ix_quota_project_id ON quota (project_id)
-- statement
CREATE TABLE relation (
	id UUID NOT NULL,
	project_id UUID NOT NULL,
	src_type VARCHAR(48) NOT NULL,
	src_id UUID NOT NULL,
	dst_type VARCHAR(48) NOT NULL,
	dst_id UUID NOT NULL,
	rel_type VARCHAR(16) NOT NULL,
	ref_mode VARCHAR(16),
	pinned_version_id UUID,
	ordinal INTEGER,
	metadata JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE
)
-- statement
CREATE INDEX ix_relation_dst ON relation (dst_type, dst_id)
-- statement
CREATE INDEX ix_relation_project_id ON relation (project_id)
-- statement
CREATE INDEX ix_relation_src ON relation (src_type, src_id)
-- statement
CREATE TABLE review (
	id UUID NOT NULL,
	project_id UUID NOT NULL,
	target_type VARCHAR(16) NOT NULL,
	target_id UUID NOT NULL,
	generation_id UUID,
	status VARCHAR(16) NOT NULL,
	round_no INTEGER NOT NULL,
	requested_by UUID,
	reviewer_id UUID,
	decision TEXT,
	resolved_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE
)
-- statement
CREATE INDEX ix_review_project_id ON review (project_id)
-- statement
CREATE INDEX ix_review_target ON review (target_type, target_id)
-- statement
CREATE TABLE timeline (
	id UUID NOT NULL,
	project_id UUID NOT NULL,
	name VARCHAR(255) NOT NULL,
	kind VARCHAR(16) NOT NULL,
	status VARCHAR(16) NOT NULL,
	created_by UUID,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	deleted_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE
)
-- statement
CREATE INDEX ix_timeline_project_id ON timeline (project_id)
-- statement
CREATE TABLE version (
	id UUID NOT NULL,
	project_id UUID NOT NULL,
	entity_type VARCHAR(48) NOT NULL,
	entity_id UUID NOT NULL,
	version_no INTEGER NOT NULL,
	label VARCHAR(128),
	status VARCHAR(16) NOT NULL,
	content JSONB NOT NULL,
	is_locked BOOLEAN NOT NULL,
	created_by UUID,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE,
	CONSTRAINT uq_version_no UNIQUE (entity_type, entity_id, version_no)
)
-- statement
CREATE INDEX ix_version_entity ON version (entity_type, entity_id)
-- statement
CREATE INDEX ix_version_project_id ON version (project_id)
-- statement
CREATE TABLE annotation (
	id UUID NOT NULL,
	review_id UUID NOT NULL,
	kind VARCHAR(16) NOT NULL,
	geometry JSONB,
	timecode_ms INTEGER,
	comment TEXT NOT NULL,
	author_id UUID,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(review_id) REFERENCES review (id) ON DELETE CASCADE
)
-- statement
CREATE TABLE asset_reference_image (
	id UUID NOT NULL,
	asset_id UUID NOT NULL,
	blob_hash VARCHAR(128) NOT NULL,
	role VARCHAR(32) NOT NULL,
	note VARCHAR(255),
	ordinal INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(blob_hash) REFERENCES blob (hash),
	FOREIGN KEY(asset_id) REFERENCES asset (id) ON DELETE CASCADE
)
-- statement
CREATE TABLE audio_track (
	id UUID NOT NULL,
	timeline_id UUID NOT NULL,
	kind VARCHAR(16) NOT NULL,
	blob_hash VARCHAR(128),
	config JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(timeline_id) REFERENCES timeline (id) ON DELETE CASCADE,
	FOREIGN KEY(blob_hash) REFERENCES blob (hash)
)
-- statement
CREATE TABLE baseline_item (
	id UUID NOT NULL,
	baseline_id UUID NOT NULL,
	entity_type VARCHAR(48) NOT NULL,
	entity_id UUID NOT NULL,
	version_id UUID NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(baseline_id) REFERENCES baseline (id) ON DELETE CASCADE,
	CONSTRAINT uq_baseline_item UNIQUE (baseline_id, entity_type, entity_id)
)
-- statement
CREATE TABLE chapter (
	id UUID NOT NULL,
	novel_id UUID NOT NULL,
	ordinal INTEGER NOT NULL,
	title VARCHAR(255),
	content TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(novel_id) REFERENCES novel (id) ON DELETE CASCADE
)
-- statement
CREATE INDEX ix_chapter_novel_ordinal ON chapter (novel_id, ordinal)
-- statement
CREATE TABLE cut (
	id UUID NOT NULL,
	project_id UUID NOT NULL,
	name VARCHAR(255) NOT NULL,
	kind VARCHAR(16) NOT NULL,
	timeline_id UUID NOT NULL,
	baseline_id UUID,
	status VARCHAR(16) NOT NULL,
	created_by UUID,
	code VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE,
	FOREIGN KEY(timeline_id) REFERENCES timeline (id)
)
-- statement
CREATE INDEX ix_cut_project_id ON cut (project_id)
-- statement
CREATE TABLE generation (
	id UUID NOT NULL,
	project_id UUID NOT NULL,
	job_id UUID NOT NULL,
	target_type VARCHAR(16) NOT NULL,
	target_id UUID NOT NULL,
	provider VARCHAR(64) NOT NULL,
	model VARCHAR(128),
	seed INTEGER,
	params JSONB NOT NULL,
	prompt_snapshot TEXT NOT NULL,
	input_refs JSONB NOT NULL,
	output_blob_hash VARCHAR(128),
	output_type VARCHAR(16) NOT NULL,
	thumbnail_hash VARCHAR(128),
	rating INTEGER,
	is_favorite BOOLEAN NOT NULL,
	is_selected BOOLEAN NOT NULL,
	cost_points NUMERIC(14, 2) NOT NULL,
	cost_raw JSONB,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(output_blob_hash) REFERENCES blob (hash),
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE,
	FOREIGN KEY(thumbnail_hash) REFERENCES blob (hash),
	FOREIGN KEY(job_id) REFERENCES generation_job (id)
)
-- statement
CREATE INDEX ix_generation_project_id ON generation (project_id)
-- statement
CREATE INDEX ix_generation_target ON generation (target_type, target_id)
-- statement
CREATE TABLE visual_identity (
	id UUID NOT NULL,
	asset_id UUID NOT NULL,
	strategy VARCHAR(16) NOT NULL,
	prompt_fragment_id UUID,
	lora_ref JSONB,
	reference_set JSONB,
	config JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (asset_id),
	FOREIGN KEY(prompt_fragment_id) REFERENCES prompt_fragment (id),
	FOREIGN KEY(asset_id) REFERENCES asset (id) ON DELETE CASCADE
)
-- statement
CREATE TABLE script (
	id UUID NOT NULL,
	project_id UUID NOT NULL,
	title VARCHAR(255) NOT NULL,
	status VARCHAR(16) NOT NULL,
	current_version_id UUID,
	created_by UUID,
	code VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	deleted_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE
)
-- statement
CREATE INDEX ix_script_project_id ON script (project_id)
-- statement
CREATE TABLE scene (
	id UUID NOT NULL,
	project_id UUID NOT NULL,
	script_id UUID NOT NULL,
	ordinal INTEGER NOT NULL,
	title VARCHAR(255),
	summary TEXT,
	body TEXT NOT NULL,
	adapted_from_chapter_id UUID,
	status VARCHAR(16) NOT NULL,
	current_version_id UUID,
	created_by UUID,
	code VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	deleted_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	FOREIGN KEY(adapted_from_chapter_id) REFERENCES chapter (id),
	FOREIGN KEY(script_id) REFERENCES script (id) ON DELETE CASCADE,
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE
)
-- statement
CREATE INDEX ix_scene_project_id ON scene (project_id)
-- statement
CREATE INDEX ix_scene_script_ordinal ON scene (script_id, ordinal)
-- statement
CREATE TABLE shot (
	id UUID NOT NULL,
	project_id UUID NOT NULL,
	scene_id UUID NOT NULL,
	ordinal INTEGER NOT NULL,
	title VARCHAR(255),
	description TEXT NOT NULL,
	production_status VARCHAR(24) NOT NULL,
	prompt_override TEXT,
	selected_generation_id UUID,
	assignee_id UUID,
	reviewer_id UUID,
	status VARCHAR(16) NOT NULL,
	current_version_id UUID,
	created_by UUID,
	code VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	deleted_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	FOREIGN KEY(reviewer_id) REFERENCES "user" (id),
	FOREIGN KEY(scene_id) REFERENCES scene (id) ON DELETE CASCADE,
	FOREIGN KEY(project_id) REFERENCES project (id) ON DELETE CASCADE,
	FOREIGN KEY(assignee_id) REFERENCES "user" (id)
)
-- statement
CREATE INDEX ix_shot_board ON shot (project_id, production_status)
-- statement
CREATE INDEX ix_shot_project_id ON shot (project_id)
-- statement
CREATE INDEX ix_shot_scene_ordinal ON shot (scene_id, ordinal)
-- statement
CREATE TABLE revision (
	id UUID NOT NULL,
	review_id UUID NOT NULL,
	shot_id UUID NOT NULL,
	round_no INTEGER NOT NULL,
	note TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(shot_id) REFERENCES shot (id),
	FOREIGN KEY(review_id) REFERENCES review (id) ON DELETE CASCADE
)
-- statement
CREATE TABLE shot_asset_ref (
	id UUID NOT NULL,
	shot_id UUID NOT NULL,
	asset_id UUID NOT NULL,
	ref_mode VARCHAR(16) NOT NULL,
	pinned_version_id UUID,
	role VARCHAR(32) NOT NULL,
	ordinal INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(asset_id) REFERENCES asset (id),
	FOREIGN KEY(shot_id) REFERENCES shot (id) ON DELETE CASCADE,
	CONSTRAINT uq_shot_asset_ref UNIQUE (shot_id, asset_id, role)
)
-- statement
CREATE INDEX ix_shot_asset_ref_asset_id ON shot_asset_ref (asset_id)
-- statement
CREATE TABLE timeline_item (
	id UUID NOT NULL,
	timeline_id UUID NOT NULL,
	shot_id UUID NOT NULL,
	generation_id UUID,
	ordinal INTEGER NOT NULL,
	in_point_ms INTEGER NOT NULL,
	out_point_ms INTEGER NOT NULL,
	duration_ms INTEGER NOT NULL,
	transition JSONB,
	note VARCHAR(255),
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(shot_id) REFERENCES shot (id),
	FOREIGN KEY(timeline_id) REFERENCES timeline (id) ON DELETE CASCADE
)
-- statement
CREATE INDEX ix_timeline_item_order ON timeline_item (timeline_id, ordinal)
