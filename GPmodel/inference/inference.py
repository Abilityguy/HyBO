import numpy as np
import torch
import torch.nn as nn


class Inference(nn.Module):
    def __init__(self, train_data, model):
        super(Inference, self).__init__()
        self.model = model
        self.train_x = train_data[0]
        self.train_y = train_data[1]
        self.output_min = torch.min(self.train_y)
        self.output_max = torch.max(self.train_y)
        self.mean_vec = None
        self.gram_mat = None
        # cholesky is lower triangular matrix
        self.cholesky = None
        self.jitter = 0

    def gram_mat_update(self, hyper=None):
        if hyper is not None:
            self.model.vec_to_param(hyper)

        self.mean_vec = self.train_y - self.model.mean(self.train_x.float())
        self.gram_mat = self.model.kernel(self.train_x) + torch.diag(self.model.likelihood(self.train_x.float()))

    def cholesky_update(self, hyper):
        self.gram_mat_update(hyper)
        eye_mat = torch.diag(self.gram_mat.new_ones(self.gram_mat.size(0)))
        for jitter_const in [0, 1e-8, 1e-7, 1e-6, 1e-5, 1e-4, 1e-3]:
            chol_jitter = torch.trace(self.gram_mat).item() * jitter_const
            try:
                # cholesky is lower triangular matrix
                self.cholesky = torch.linalg.cholesky(self.gram_mat + eye_mat * chol_jitter, upper=False)
                self.jitter = chol_jitter
                return
            except RuntimeError:
                pass
        raise RuntimeError(
            "Absolute entry values of Gram matrix are between %.4E~%.4E with trace %.4E"
            % (
                torch.min(torch.abs(self.gram_mat)).item(),
                torch.max(torch.abs(self.gram_mat)).item(),
                torch.trace(self.gram_mat).item(),
            )
        )

    def predict(self, pred_x, hyper=None, verbose=False, compute_grad=False):
        if hyper is not None:
            param_original = self.model.param_to_vec()
            self.cholesky_update(hyper)

        k_pred_train = self.model.kernel(pred_x, self.train_x)
        k_pred = self.model.kernel(pred_x, diagonal=True)

        # cholesky is lower triangular matrix
        chol_solver = torch.linalg.solve_triangular(
            self.cholesky, torch.cat([k_pred_train.t(), self.mean_vec], 1), upper=False
        )
        chol_solve_k = chol_solver[:, :-1]
        chol_solve_y = chol_solver[:, -1:]

        pred_mean = torch.mm(chol_solve_k.t(), chol_solve_y) + self.model.mean(pred_x)
        pred_quad = (chol_solve_k**2).sum(0).view(-1, 1)
        pred_var = k_pred - pred_quad

        if verbose:
            numerically_stable = (pred_var >= 0).all()
            zero_pred_var = (pred_var <= 0).all()

        if hyper is not None:
            self.cholesky_update(param_original)

        if compute_grad:
            alpha = torch.cholesky_solve(self.mean_vec, self.cholesky, upper=False)
            grad_cross = self.model.kernel.grad(self.train_x, pred_x)
            grad_xp_m = torch.mm(grad_cross, k_pred_train.t() * alpha)
            gamma = torch.triangular_solve(chol_solve_k, self.cholesky.t(), upper=True)[0]
            grad_xp_v = -2 * torch.mm(gamma.t(), (grad_cross * k_pred_train).t()).t()
            return pred_mean, pred_var.clamp(min=1e-8), grad_xp_m, grad_xp_v
        else:
            if verbose:
                return pred_mean, pred_var.clamp(min=1e-8), numerically_stable, zero_pred_var
            else:
                return pred_mean, pred_var.clamp(min=1e-8)

    def negative_log_likelihood(self, hyper=None):
        if hyper is not None:
            param_original = self.model.param_to_vec()
            self.cholesky_update(hyper)

        # cholesky is lower triangular matrix
        mean_vec_sol = torch.linalg.solve_triangular(self.cholesky, self.mean_vec, upper=False)
        nll = (
            0.5 * torch.sum(mean_vec_sol**2)
            + torch.sum(torch.log(torch.diag(self.cholesky)))
            + 0.5 * self.train_y.size(0) * np.log(2 * np.pi)
        )
        if hyper is not None:
            self.cholesky_update(param_original)
        return nll


if __name__ == "__main__":
    n_size_ = 50
    jitter_const_ = 0
    for _ in range(10):
        A_ = torch.randn(n_size_, n_size_ - 2)
        A_ = A_.matmul(A_.t()) * 0 + 1e-6
        A_ = A_ + torch.diag(torch.ones(n_size_)) * jitter_const_ * torch.trace(A_).item()
        b_ = torch.randn(n_size_, 3)
        L_ = torch.linalg.cholesky(A_, upper=False)
        assert (torch.diag(L_) > 0).all()
        abs_min = torch.min(torch.abs(A_)).item()
        abs_max = torch.max(torch.abs(A_)).item()
        trace = torch.trace(A_).item()
        print("            %.4E~%.4E      %.4E" % (abs_min, abs_max, trace))
        print("     jitter:%.4E" % (trace * jitter_const_))
        print("The smallest eigen value : %.4E\n" % torch.min(torch.diag(L_)).item())
        torch.linalg.solve_triangular(L_, b_, upper=False)
