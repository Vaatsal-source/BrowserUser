# Dev Privacy Guard — LaTeX research draft

Open `main.tex` in Overleaf, or compile locally with `latexmk -pdf main.tex` (alternatively run `pdflatex main.tex` twice). Tectonic is also supported. Figures are native TikZ; bibliography entries are included in the source. No separate images or `.bib` file are required.

## Basis and scope

The paper is based on the intended architecture in `REAL_TECHNICAL_PLAN.md` and `docs/TECHNICAL_FLOWCHART.md`, not an account of the current product or its test results.

The supplied DECIBEL PDF was inspected. Its section and subsection wording and order have been retained, including the audio-specific numbered headings under Model Architecture. A note in the manuscript explains their browser-agent counterparts. The Conclusion is unnumbered, as in the reference. Figures, algorithm, equations, listing, tables, and original prose were created for this project; unrelated audio performance claims and references were not copied.

All performance numbers are manually invented examples, explicitly labeled in the abstract, results introduction, table captions, and conclusion. They are not observations, simulations, estimates derived from data, or evidence of statistical significance. Dataset counts and training settings are proposed plans. References point to real primary sources checked during drafting.

## Authors

All six supplied authors appear in the requested order with their exact DTU email addresses. The reference uses name, department (where supplied), university, city, and institutional email; it does not use roll number, phone, personal email, LinkedIn, GitHub, or Kaggle, so those fields are not printed.

The first four department labels follow the reference's wording and the supplied academic identifiers: Applied Mathematics, Computer Science, Electrical Engineering, and Software Engineering. Vaatsalya Srivastava and Shreya Kailash use university and city only, avoiding an unconfirmed formal department name; the reference itself also contains an author with university and city only. Verify formal affiliations before submission.

## Heading inventory

- Abstract; Index Terms
- I. Introduction
- II. Related Works
- III. Methodology
  - A. Overview of the Proposed Approach
  - B. Dataset Construction
    - 1) Scenario-Based and Monte Carlo Augmentation
    - 2) Multilingual Q&A Generation
  - C. Model Architecture
    - 1) Overview of the Mixture-of-Experts Framework
    - 2) Multilingual ASR and Speech Embedding Expert
    - 3) Speaker Diarization and Identity Encoding
    - 4) Audio Event and Paralinguistic Experts
    - 5) Temporal-Semantic Fusion and Reasoning Head
  - D. Training Strategy and Optimization
- IV. Experimental Setup and Evaluation Protocol
  - A. Dataset Composition and Preprocessing
  - B. Baseline Models for Comparison
  - C. Evaluation Metrics
  - D. Implementation Details
- V. Results and Discussion
  - A. Quantitative Evaluation
    - Metric-specific Insights.
  - B. Ablation Analysis
  - C. Qualitative Analysis and Interpretability
  - D. Discussion
- Conclusion
- References

For a future submission, replace the invented results with actual runs and consider adapting the audio-specific heading titles to the browser-agent domain.
