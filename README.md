# public-evals
Benchmarks and publicly published evals

## Benchmarks

**[diff-bwm-1](benchmarks/diff-bwm-1/README.md)** is a six-eval suite that tests whether an LLM planning agent that can run a numerical model of its domain behaves more like a programmer with code: internally consistent, and able to test a draft before shipping it. Each eval puts a live coding agent in a business-planning sandbox, from a single company to coupled portfolios, self-built models, third-party spreadsheets, prose consulting deliverables and withheld causal information, and a scorer checks the plan and whether the agent's claim of success was true. The package ships the problems, the answers and the code that stages, runs, scores, audits and reports runs. Version 1.0.0; Python package `dbwm`.

## License

The code in this repository is licensed under the MIT License. The benchmark scenarios, datasets, and evaluative text prompts are licensed under Creative Commons Attribution 4.0 International (CC BY 4.0).

See [LICENSE](LICENSE) and [LICENSE-DATA](LICENSE-DATA). Third-party material keeps its own license; see each benchmark's `NOTICE`.

Exception: eval 4 of diff-bwm-1 is built on [AA-Briefcase-Lite](https://huggingface.co/datasets/ArtificialAnalysis/AA-Briefcase-Lite) by Artificial Analysis, licensed under the Apache License 2.0. The dataset is not redistributed here; it is downloaded from its publisher at a pinned revision. Material derived from it stays under Apache-2.0, not CC BY 4.0. If you use eval 4, also cite AA-Briefcase-Lite.
