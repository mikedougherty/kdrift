# Changelog

## [0.1.7](https://github.com/mikedougherty/kdrift/compare/v0.1.6...v0.1.7) (2026-10-02)


### Documentation

* note that the PyPI publish needs a manual dispatch ([268f3a6](https://github.com/mikedougherty/kdrift/commit/268f3a6a7cefa3703b616ef545933fb17ffadc78))

## [0.1.6](https://github.com/mikedougherty/kdrift/compare/v0.1.5...v0.1.6) (2026-10-02)


### Features

* add --chart-home override to diff uncommitted chartHome redirects ([88e5c35](https://github.com/mikedougherty/kdrift/commit/88e5c352ae1cd0ee017ebbbadf38fd23fe33edcd))
* add --version flag and version subcommand ([4eac2fa](https://github.com/mikedougherty/kdrift/commit/4eac2fa9af0ee5570c25166a5d70c95a655926db))
* diff out-of-repo helm chart sources via multi-repo worktrees ([db6a53c](https://github.com/mikedougherty/kdrift/commit/db6a53c3cd1828cb5a706d65476e7e13361ed076))
* follow helm chart dirs and more local pointers in dependency graph ([2fb6d6b](https://github.com/mikedougherty/kdrift/commit/2fb6d6b5e9a6724b8bf5c8caebac2dc4978bfa9f))
* resolve helm dependencies in the baseline worktree ([ce6bc6c](https://github.com/mikedougherty/kdrift/commit/ce6bc6c28375a0f51acd3c5e06c5921025e17707))


### Bug Fixes

* strip repo: when --chart-home overrides a chart's source ([b73e571](https://github.com/mikedougherty/kdrift/commit/b73e5718969b332cedebb610cb10adb41f1439f1))
* surface underlying stderr on baseline build failure ([538bb30](https://github.com/mikedougherty/kdrift/commit/538bb3058b0cf6b617d56e0a4a66f6584dd5db22))


### Documentation

* document release + RC process in the development guide ([b30676c](https://github.com/mikedougherty/kdrift/commit/b30676c63a8204fd449877bd2b23181f01d5152f))

## [0.1.5](https://github.com/mikedougherty/kdrift/compare/v0.1.4...v0.1.5) (2026-09-16)


### Bug Fixes

* migrate MCP server to mcp 2.x API ([d47cee7](https://github.com/mikedougherty/kdrift/commit/d47cee7fc330fb002ad1887cf597f2ad46a76aca))

## [0.1.4](https://github.com/mikedougherty/kdrift/compare/v0.1.3...v0.1.4) (2026-09-15)


### Bug Fixes

* select diff overlays by path instead of git-pathspec-filtering changes ([9985482](https://github.com/mikedougherty/kdrift/commit/998548227d0b1b3b78176d0c7a059f7dc2f0fea1)), closes [#7](https://github.com/mikedougherty/kdrift/issues/7)


### Documentation

* clarify diff paths semantics (overlay selection, not pathspec) ([17187c5](https://github.com/mikedougherty/kdrift/commit/17187c5c1a58ebe7b1d3892e1058d632c7508b25))

## [0.1.3](https://github.com/mikedougherty/kdrift/compare/v0.1.2...v0.1.3) (2026-06-04)


### Features

* add kustomize environment variable injection ([d668028](https://github.com/mikedougherty/kdrift/commit/d66802802dcc18e26de0e2c0b35b91d819567e30))


### Documentation

* add VS Code Marketplace publishing plan ([31d5726](https://github.com/mikedougherty/kdrift/commit/31d57268d7a6d6777997b92bac7b4710037b8734))
* clarify copyright ownership in license ([8c396b2](https://github.com/mikedougherty/kdrift/commit/8c396b2f9a7f75835296aeca9a210cf7ae312e44))
