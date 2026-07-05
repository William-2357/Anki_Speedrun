// this file is generated — do not edit it

/// <reference types="@sveltejs/kit" />

/**
 * This module provides access to environment variables that are injected _statically_ into your bundle at build time and are limited to _private_ access.
 *
 * |         | Runtime                                                                    | Build time                                                               |
 * | ------- | -------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
 * | Private | [`$env/dynamic/private`](https://svelte.dev/docs/kit/$env-dynamic-private) | [`$env/static/private`](https://svelte.dev/docs/kit/$env-static-private) |
 * | Public  | [`$env/dynamic/public`](https://svelte.dev/docs/kit/$env-dynamic-public)   | [`$env/static/public`](https://svelte.dev/docs/kit/$env-static-public)   |
 *
 * Static environment variables are [loaded by Vite](https://vitejs.dev/guide/env-and-mode.html#env-files) from `.env` files and `process.env` at build time and then statically injected into your bundle at build time, enabling optimisations like dead code elimination.
 *
 * **_Private_ access:**
 *
 * - This module cannot be imported into client-side code
 * - This module only includes variables that _do not_ begin with [`config.kit.env.publicPrefix`](https://svelte.dev/docs/kit/configuration#env) _and do_ start with [`config.kit.env.privatePrefix`](https://svelte.dev/docs/kit/configuration#env) (if configured)
 *
 * For example, given the following build time environment:
 *
 * ```env
 * ENVIRONMENT=production
 * PUBLIC_BASE_URL=http://site.com
 * ```
 *
 * With the default `publicPrefix` and `privatePrefix`:
 *
 * ```ts
 * import { ENVIRONMENT, PUBLIC_BASE_URL } from '$env/static/private';
 *
 * console.log(ENVIRONMENT); // => "production"
 * console.log(PUBLIC_BASE_URL); // => throws error during build
 * ```
 *
 * The above values will be the same _even if_ different values for `ENVIRONMENT` or `PUBLIC_BASE_URL` are set at runtime, as they are statically replaced in your code with their build time values.
 */
declare module "$env/static/private" {
    export const ANDROID_HOME: string;
    export const ANDROID_SDK_ROOT: string;
    export const COMMAND_MODE: string;
    export const CONDA_DEFAULT_ENV: string;
    export const CONDA_EXE: string;
    export const CONDA_PREFIX: string;
    export const CONDA_PROMPT_MODIFIER: string;
    export const CONDA_PYTHON_EXE: string;
    export const CONDA_SHLVL: string;
    export const CURSOR_LAYOUT: string;
    export const CURSOR_WORKSPACE_LABEL: string;
    export const FPATH: string;
    export const GSETTINGS_SCHEMA_DIR: string;
    export const HOME: string;
    export const HOMEBREW_CELLAR: string;
    export const HOMEBREW_PREFIX: string;
    export const HOMEBREW_REPOSITORY: string;
    export const INFOPATH: string;
    export const JAVA_HOME: string;
    export const LANG: string;
    export const LOGNAME: string;
    export const LaunchInstanceID: string;
    export const MACH_PORT_RENDEZVOUS_PEER_VALDATION: string;
    export const MANPATH: string;
    export const MallocNanoZone: string;
    export const OLDPWD: string;
    export const OSLogRateLimit: string;
    export const PATH: string;
    export const PWD: string;
    export const SECURITYSESSIONID: string;
    export const SHELL: string;
    export const SHLVL: string;
    export const SSH_AUTH_SOCK: string;
    export const TMPDIR: string;
    export const USER: string;
    export const VSCODE_CODE_CACHE_PATH: string;
    export const VSCODE_CRASH_REPORTER_PROCESS_TYPE: string;
    export const VSCODE_CWD: string;
    export const VSCODE_ESM_ENTRYPOINT: string;
    export const VSCODE_HANDLES_UNCAUGHT_ERRORS: string;
    export const VSCODE_IPC_HOOK: string;
    export const VSCODE_NLS_CONFIG: string;
    export const VSCODE_PID: string;
    export const VSCODE_PROCESS_TITLE: string;
    export const XML_CATALOG_FILES: string;
    export const XPC_FLAGS: string;
    export const XPC_SERVICE_NAME: string;
    export const __CFBundleIdentifier: string;
    export const __CF_USER_TEXT_ENCODING: string;
    export const NPM_CONFIG_CACHE: string;
    export const PNPM_STORE_PATH: string;
    export const GOCACHE: string;
    export const GOMODCACHE: string;
    export const CARGO_TARGET_DIR: string;
    export const PIP_CACHE_DIR: string;
    export const UV_CACHE_DIR: string;
    export const BUN_INSTALL_CACHE_DIR: string;
    export const YARN_CACHE_FOLDER: string;
    export const npm_config_devdir: string;
    export const PLAYWRIGHT_BROWSERS_PATH: string;
    export const PUPPETEER_CACHE_DIR: string;
    export const TURBO_CACHE_DIR: string;
    export const GRADLE_USER_HOME: string;
    export const CONDA_PKGS_DIRS: string;
    export const POETRY_CACHE_DIR: string;
    export const GEM_SPEC_CACHE: string;
    export const BUNDLE_PATH: string;
    export const COMPOSER_HOME: string;
    export const HOMEBREW_CACHE: string;
    export const CYPRESS_CACHE_FOLDER: string;
    export const NX_CACHE_DIRECTORY: string;
    export const NUGET_PACKAGES: string;
    export const CCACHE_DIR: string;
    export const CP_HOME_DIR: string;
    export const TERM: string;
    export const NO_COLOR: string;
    export const FORCE_COLOR: string;
    export const _ZO_DOCTOR: string;
    export const CURSOR_AGENT: string;
    export const CURSOR_CONVERSATION_ID: string;
    export const AGENT_TRANSCRIPTS: string;
    export const CURSOR_RIPGREP_PATH: string;
    export const CURSOR_ORIG_GID: string;
    export const CURSOR_ORIG_UID: string;
    export const ELECTRON_RUN_AS_NODE: string;
    export const GSETTINGS_SCHEMA_DIR_CONDA_BACKUP: string;
    export const SCRATCH: string;
    export const _CE_CONDA: string;
    export const _CE_M: string;
    export const _: string;
    export const TEST: string;
    export const VITEST: string;
    export const NODE_ENV: string;
    export const PROD: string;
    export const DEV: string;
    export const BASE_URL: string;
    export const MODE: string;
}

/**
 * This module provides access to environment variables that are injected _statically_ into your bundle at build time and are _publicly_ accessible.
 *
 * |         | Runtime                                                                    | Build time                                                               |
 * | ------- | -------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
 * | Private | [`$env/dynamic/private`](https://svelte.dev/docs/kit/$env-dynamic-private) | [`$env/static/private`](https://svelte.dev/docs/kit/$env-static-private) |
 * | Public  | [`$env/dynamic/public`](https://svelte.dev/docs/kit/$env-dynamic-public)   | [`$env/static/public`](https://svelte.dev/docs/kit/$env-static-public)   |
 *
 * Static environment variables are [loaded by Vite](https://vitejs.dev/guide/env-and-mode.html#env-files) from `.env` files and `process.env` at build time and then statically injected into your bundle at build time, enabling optimisations like dead code elimination.
 *
 * **_Public_ access:**
 *
 * - This module _can_ be imported into client-side code
 * - **Only** variables that begin with [`config.kit.env.publicPrefix`](https://svelte.dev/docs/kit/configuration#env) (which defaults to `PUBLIC_`) are included
 *
 * For example, given the following build time environment:
 *
 * ```env
 * ENVIRONMENT=production
 * PUBLIC_BASE_URL=http://site.com
 * ```
 *
 * With the default `publicPrefix` and `privatePrefix`:
 *
 * ```ts
 * import { ENVIRONMENT, PUBLIC_BASE_URL } from '$env/static/public';
 *
 * console.log(ENVIRONMENT); // => throws error during build
 * console.log(PUBLIC_BASE_URL); // => "http://site.com"
 * ```
 *
 * The above values will be the same _even if_ different values for `ENVIRONMENT` or `PUBLIC_BASE_URL` are set at runtime, as they are statically replaced in your code with their build time values.
 */
declare module "$env/static/public" {
}

/**
 * This module provides access to environment variables set _dynamically_ at runtime and that are limited to _private_ access.
 *
 * |         | Runtime                                                                    | Build time                                                               |
 * | ------- | -------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
 * | Private | [`$env/dynamic/private`](https://svelte.dev/docs/kit/$env-dynamic-private) | [`$env/static/private`](https://svelte.dev/docs/kit/$env-static-private) |
 * | Public  | [`$env/dynamic/public`](https://svelte.dev/docs/kit/$env-dynamic-public)   | [`$env/static/public`](https://svelte.dev/docs/kit/$env-static-public)   |
 *
 * Dynamic environment variables are defined by the platform you're running on. For example if you're using [`adapter-node`](https://github.com/sveltejs/kit/tree/main/packages/adapter-node) (or running [`vite preview`](https://svelte.dev/docs/kit/cli)), this is equivalent to `process.env`.
 *
 * **_Private_ access:**
 *
 * - This module cannot be imported into client-side code
 * - This module includes variables that _do not_ begin with [`config.kit.env.publicPrefix`](https://svelte.dev/docs/kit/configuration#env) _and do_ start with [`config.kit.env.privatePrefix`](https://svelte.dev/docs/kit/configuration#env) (if configured)
 *
 * > [!NOTE] In `dev`, `$env/dynamic` includes environment variables from `.env`. In `prod`, this behavior will depend on your adapter.
 *
 * > [!NOTE] To get correct types, environment variables referenced in your code should be declared (for example in an `.env` file), even if they don't have a value until the app is deployed:
 * >
 * > ```env
 * > MY_FEATURE_FLAG=
 * > ```
 * >
 * > You can override `.env` values from the command line like so:
 * >
 * > ```sh
 * > MY_FEATURE_FLAG="enabled" npm run dev
 * > ```
 *
 * For example, given the following runtime environment:
 *
 * ```env
 * ENVIRONMENT=production
 * PUBLIC_BASE_URL=http://site.com
 * ```
 *
 * With the default `publicPrefix` and `privatePrefix`:
 *
 * ```ts
 * import { env } from '$env/dynamic/private';
 *
 * console.log(env.ENVIRONMENT); // => "production"
 * console.log(env.PUBLIC_BASE_URL); // => undefined
 * ```
 */
declare module "$env/dynamic/private" {
    export const env: {
        ANDROID_HOME: string;
        ANDROID_SDK_ROOT: string;
        COMMAND_MODE: string;
        CONDA_DEFAULT_ENV: string;
        CONDA_EXE: string;
        CONDA_PREFIX: string;
        CONDA_PROMPT_MODIFIER: string;
        CONDA_PYTHON_EXE: string;
        CONDA_SHLVL: string;
        CURSOR_LAYOUT: string;
        CURSOR_WORKSPACE_LABEL: string;
        FPATH: string;
        GSETTINGS_SCHEMA_DIR: string;
        HOME: string;
        HOMEBREW_CELLAR: string;
        HOMEBREW_PREFIX: string;
        HOMEBREW_REPOSITORY: string;
        INFOPATH: string;
        JAVA_HOME: string;
        LANG: string;
        LOGNAME: string;
        LaunchInstanceID: string;
        MACH_PORT_RENDEZVOUS_PEER_VALDATION: string;
        MANPATH: string;
        MallocNanoZone: string;
        OLDPWD: string;
        OSLogRateLimit: string;
        PATH: string;
        PWD: string;
        SECURITYSESSIONID: string;
        SHELL: string;
        SHLVL: string;
        SSH_AUTH_SOCK: string;
        TMPDIR: string;
        USER: string;
        VSCODE_CODE_CACHE_PATH: string;
        VSCODE_CRASH_REPORTER_PROCESS_TYPE: string;
        VSCODE_CWD: string;
        VSCODE_ESM_ENTRYPOINT: string;
        VSCODE_HANDLES_UNCAUGHT_ERRORS: string;
        VSCODE_IPC_HOOK: string;
        VSCODE_NLS_CONFIG: string;
        VSCODE_PID: string;
        VSCODE_PROCESS_TITLE: string;
        XML_CATALOG_FILES: string;
        XPC_FLAGS: string;
        XPC_SERVICE_NAME: string;
        __CFBundleIdentifier: string;
        __CF_USER_TEXT_ENCODING: string;
        NPM_CONFIG_CACHE: string;
        PNPM_STORE_PATH: string;
        GOCACHE: string;
        GOMODCACHE: string;
        CARGO_TARGET_DIR: string;
        PIP_CACHE_DIR: string;
        UV_CACHE_DIR: string;
        BUN_INSTALL_CACHE_DIR: string;
        YARN_CACHE_FOLDER: string;
        npm_config_devdir: string;
        PLAYWRIGHT_BROWSERS_PATH: string;
        PUPPETEER_CACHE_DIR: string;
        TURBO_CACHE_DIR: string;
        GRADLE_USER_HOME: string;
        CONDA_PKGS_DIRS: string;
        POETRY_CACHE_DIR: string;
        GEM_SPEC_CACHE: string;
        BUNDLE_PATH: string;
        COMPOSER_HOME: string;
        HOMEBREW_CACHE: string;
        CYPRESS_CACHE_FOLDER: string;
        NX_CACHE_DIRECTORY: string;
        NUGET_PACKAGES: string;
        CCACHE_DIR: string;
        CP_HOME_DIR: string;
        TERM: string;
        NO_COLOR: string;
        FORCE_COLOR: string;
        _ZO_DOCTOR: string;
        CURSOR_AGENT: string;
        CURSOR_CONVERSATION_ID: string;
        AGENT_TRANSCRIPTS: string;
        CURSOR_RIPGREP_PATH: string;
        CURSOR_ORIG_GID: string;
        CURSOR_ORIG_UID: string;
        ELECTRON_RUN_AS_NODE: string;
        GSETTINGS_SCHEMA_DIR_CONDA_BACKUP: string;
        SCRATCH: string;
        _CE_CONDA: string;
        _CE_M: string;
        _: string;
        TEST: string;
        VITEST: string;
        NODE_ENV: string;
        PROD: string;
        DEV: string;
        BASE_URL: string;
        MODE: string;
        [key: `PUBLIC_${string}`]: undefined;
        [key: `${string}`]: string | undefined;
    };
}

/**
 * This module provides access to environment variables set _dynamically_ at runtime and that are _publicly_ accessible.
 *
 * |         | Runtime                                                                    | Build time                                                               |
 * | ------- | -------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
 * | Private | [`$env/dynamic/private`](https://svelte.dev/docs/kit/$env-dynamic-private) | [`$env/static/private`](https://svelte.dev/docs/kit/$env-static-private) |
 * | Public  | [`$env/dynamic/public`](https://svelte.dev/docs/kit/$env-dynamic-public)   | [`$env/static/public`](https://svelte.dev/docs/kit/$env-static-public)   |
 *
 * Dynamic environment variables are defined by the platform you're running on. For example if you're using [`adapter-node`](https://github.com/sveltejs/kit/tree/main/packages/adapter-node) (or running [`vite preview`](https://svelte.dev/docs/kit/cli)), this is equivalent to `process.env`.
 *
 * **_Public_ access:**
 *
 * - This module _can_ be imported into client-side code
 * - **Only** variables that begin with [`config.kit.env.publicPrefix`](https://svelte.dev/docs/kit/configuration#env) (which defaults to `PUBLIC_`) are included
 *
 * > [!NOTE] In `dev`, `$env/dynamic` includes environment variables from `.env`. In `prod`, this behavior will depend on your adapter.
 *
 * > [!NOTE] To get correct types, environment variables referenced in your code should be declared (for example in an `.env` file), even if they don't have a value until the app is deployed:
 * >
 * > ```env
 * > MY_FEATURE_FLAG=
 * > ```
 * >
 * > You can override `.env` values from the command line like so:
 * >
 * > ```sh
 * > MY_FEATURE_FLAG="enabled" npm run dev
 * > ```
 *
 * For example, given the following runtime environment:
 *
 * ```env
 * ENVIRONMENT=production
 * PUBLIC_BASE_URL=http://example.com
 * ```
 *
 * With the default `publicPrefix` and `privatePrefix`:
 *
 * ```ts
 * import { env } from '$env/dynamic/public';
 * console.log(env.ENVIRONMENT); // => undefined, not public
 * console.log(env.PUBLIC_BASE_URL); // => "http://example.com"
 * ```
 *
 * ```
 *
 * ```
 */
declare module "$env/dynamic/public" {
    export const env: {
        [key: `PUBLIC_${string}`]: string | undefined;
    };
}
