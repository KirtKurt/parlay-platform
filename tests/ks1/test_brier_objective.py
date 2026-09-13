import numpy as np

from ks1.brier_objective import _probability, booster_params, brier_metric, brier_objective


class _Dataset:
    def __init__(self, y):
        self._y = np.asarray(y, dtype=float)

    def get_label(self):
        return self._y


def test_identity_sigmoid_and_zero_loss_when_certain_and_correct():
    p = _probability([0.0, 20.0, -20.0])
    assert abs(p[0] - 0.5) < 1e-12
    assert p[1] > 0.999999
    assert p[2] < 1e-6
    _, value, higher_better = brier_metric([20.0, -20.0], _Dataset([1.0, 0.0]))
    assert higher_better is False
    assert value < 1e-12


def test_gradient_pushes_overconfident_wrong_call_down():
    grad, hess = brier_objective([4.0], _Dataset([0.0]))
    assert grad[0] > 0
    assert hess[0] > 0


def test_booster_params_drop_builtin_objective():
    params = booster_params({"objective": "binary", "n_estimators": 200, "num_leaves": 7})
    assert "objective" not in params
    assert "n_estimators" not in params
    assert params["num_leaves"] == 7
