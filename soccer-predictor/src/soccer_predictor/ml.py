import numpy as np


class MultinomialLogit:
    def __init__(self, ridge=0.01, max_iter=60, tol=1e-7):
        self.ridge, self.max_iter, self.tol = float(ridge), int(max_iter), float(tol)
        self.mean = self.scale = self.coef = None
        self.converged, self.iterations = False, 0

    @staticmethod
    def softmax(z):
        z = z - z.max(axis=1, keepdims=True)
        e = np.exp(z)
        return e / e.sum(axis=1, keepdims=True)

    def design(self, x):
        x = np.asarray(x, dtype=float)
        if x.ndim != 2 or not np.isfinite(x).all():
            raise ValueError("Features must be a finite two-dimensional matrix")
        return np.column_stack([np.ones(len(x)), (x - self.mean) / self.scale])

    def fit(self, x, y):
        x, y = np.asarray(x, float), np.asarray(y, int)
        if x.ndim != 2 or len(x) != len(y) or not np.isfinite(x).all():
            raise ValueError("Invalid training matrix")
        if set(y.tolist()) != {0, 1, 2}:
            raise ValueError("Training needs home, draw and away examples")
        self.converged, self.iterations = False, 0
        self.mean, self.scale = x.mean(axis=0), x.std(axis=0)
        self.scale[self.scale < 1e-8] = 1.0
        a = self.design(x)
        n, d = a.shape
        self.coef = np.zeros((2, d), dtype=float)
        target = np.eye(3)[y]
        penalty = np.r_[0., np.full(d - 1, self.ridge)]

        def objective(w):
            p = self.softmax(np.column_stack([a @ w.T, np.zeros(n)]))
            loss = -np.log(np.clip(p[np.arange(n), y], 1e-15, 1)).mean()
            return loss + .5 * np.sum(w * w * penalty), p

        for step in range(self.max_iter):
            loss, p = objective(self.coef)
            g = ((p[:, :2] - target[:, :2]).T @ a) / n + self.coef * penalty
            self.iterations = step + 1
            if np.max(np.abs(g)) < self.tol:
                self.converged = True
                break
            h = np.empty((2 * d, 2 * d))
            for i in range(2):
                for j in range(2):
                    weight = p[:, i] * ((1 if i == j else 0) - p[:, j])
                    block = a.T @ (a * weight[:, None]) / n
                    if i == j:
                        block += np.diag(penalty + 1e-9)
                    h[i*d:(i+1)*d, j*d:(j+1)*d] = block
            delta = np.linalg.solve(h, g.ravel()).reshape(2, d)
            directional = float(np.sum(g * delta))
            length = 1.0
            for _ in range(30):
                candidate = self.coef - length * delta
                if objective(candidate)[0] <= loss - 1e-4 * length * directional:
                    self.coef = candidate
                    break
                length *= .5
            else:
                raise RuntimeError("Logistic line search failed")
        if not self.converged:
            _, p = objective(self.coef)
            g = ((p[:, :2] - target[:, :2]).T @ a) / n + self.coef * penalty
            self.converged = bool(np.max(np.abs(g)) < self.tol)
        if not self.converged:
            raise RuntimeError("Multinomial training did not converge")
        return self

    def predict_proba(self, x):
        if self.coef is None:
            raise ValueError("Fit the model first")
        a = self.design(x)
        return self.softmax(np.column_stack([a @ self.coef.T, np.zeros(len(a))]))

    def to_dict(self):
        return {"ridge": self.ridge, "max_iter": self.max_iter, "tol": self.tol,
                "mean": self.mean.tolist(), "scale": self.scale.tolist(),
                "coef": self.coef.tolist(), "iterations": self.iterations, "converged": self.converged}

    @classmethod
    def from_dict(cls, value):
        obj = cls(value["ridge"], value["max_iter"], value["tol"])
        for key in ["mean", "scale", "coef"]:
            setattr(obj, key, np.asarray(value[key], float))
        obj.iterations, obj.converged = value["iterations"], value["converged"]
        if not obj.converged or np.any(obj.scale <= 0) or not np.isfinite(obj.coef).all():
            raise ValueError("Invalid logistic artifact")
        return obj
