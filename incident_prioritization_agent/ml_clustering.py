"""
Unsupervised ML text clustering.

Used by prioritize.py to catch recurring incidents that DON'T have a clean
structured technical signature (no extractable Program/Abend code/SQLCODE/
Return code) for the rule-based signature matching to key off of - e.g. a
network or monitoring incident where those fields were left blank.

Model: TF-IDF vectorization + Agglomerative Hierarchical Clustering with
cosine distance and a distance threshold.

Why this model specifically, not something else:
  - Genuinely unsupervised - no labeled training data exists (or should be
    assumed to exist) for "which incidents are really the same root cause",
    so a supervised classifier isn't appropriate here.
  - Agglomerative clustering with distance_threshold (rather than KMeans)
    means we do NOT have to guess the number of clusters up front - the
    number of distinct recurring problems in a real incident stream is
    unknown and changes over time. KMeans would force every run to produce
    exactly k clusters whether that's right or not.
  - TF-IDF + cosine distance is a well-understood, fast, and explainable
    choice for small/sparse text datasets like incident short descriptions -
    unlike a large pretrained embedding model, it needs no external download,
    no GPU, and every cluster's similarity can be inspected via the actual
    shared vocabulary driving it.

Honest limitation: this is still a small-data setting. distance_threshold
is a hyperparameter that may need retuning against a larger/real incident
corpus - the default here was chosen empirically against this dataset's
free-text one-off incidents, not derived from a validation set.
"""
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import AgglomerativeClustering


def tfidf_text_clusters(texts, distance_threshold=0.6, min_df=1):
    """
    texts: list[str] - one text blob per incident (e.g. short_description +
           description), same order as the incidents being clustered.
    Returns: list[int] - a cluster label per input text, same order/length.
             Each unique label groups near-duplicate texts together.

    Degenerate cases (fewer than 2 texts, or no usable vocabulary after
    TF-IDF filtering) fall back to every text being its own singleton
    cluster, rather than erroring.
    """
    n = len(texts)
    if n < 2:
        return list(range(n))

    vectorizer = TfidfVectorizer(stop_words="english", min_df=min_df, max_df=0.95)
    try:
        matrix = vectorizer.fit_transform(texts)
    except ValueError:
        # e.g. empty vocabulary after stop-word removal on very short/odd text
        return list(range(n))

    if matrix.shape[1] == 0:
        return list(range(n))

    model = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=distance_threshold,
        metric="cosine",
        linkage="average",
    )
    labels = model.fit_predict(matrix.toarray())
    return labels.tolist()
